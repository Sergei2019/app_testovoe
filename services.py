"""Бизнес-логика: склады, документы, идемпотентные переходы, витрина остатков, сверка."""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from accounting import (MARKETPLACE, DomainError, ensure_available, ff, fbo_loc, fbs_loc,
                         post, transfer_loc)
from models import (Balance, FboShipment, FbsOrder, Operation, Posting, Product, Shop,
                     Transfer, Warehouse, utcnow)

OP_LABELS = {
    "RECEIPT": "Приход", "TRANSFER_SHIP": "Отправка перемещения",
    "TRANSFER_RECEIVE": "Приёмка перемещения", "TRANSFER_CANCEL": "Отмена перемещения",
    "FBS_RESERVE": "Резерв FBS", "FBS_SHIP": "Отправка FBS клиенту",
    "FBS_DELIVER": "Доставка клиенту", "FBS_CANCEL": "Отмена резерва FBS",
    "FBO_SHIP": "Отгрузка FBO", "FBO_ACCEPT": "Приёмка FBO на складе МП",
    "FBO_CANCEL": "Отмена отгрузки FBO", "WAREHOUSE_CREATE": "Создание ФФ",
    "WAREHOUSE_RENAME": "Переименование ФФ", "WAREHOUSE_ARCHIVE": "Архивация ФФ",
}
STATUS_LABELS = {
    "NEW": "Создан", "SHIPPED": "В пути", "RECEIVED": "Принят", "RESERVED": "В резерве",
    "DELIVERED": "Доставлен", "CANCELLED": "Отменён", "ACCEPTED": "Принят на МП",
}


# ---------- справочники ----------

def default_shop(session) -> Shop:
    return session.scalars(select(Shop).limit(1)).first()


def get_product(session, product_id: int) -> Product:
    p = session.get(Product, product_id)
    if p is None:
        raise DomainError("not_found", "Товар не найден", status_code=404)
    return p


def get_warehouse(session, warehouse_id: int) -> Warehouse:
    w = session.get(Warehouse, warehouse_id)
    if w is None:
        raise DomainError("not_found", "ФФ не найден", status_code=404)
    return w


def active_warehouse(session, warehouse_id: int) -> Warehouse:
    w = get_warehouse(session, warehouse_id)
    if w.archived_at:
        raise DomainError("warehouse_archived",
                          f"ФФ «{w.name}» в архиве: новые документы запрещены. Остатки и история сохранены.")
    return w


def get_doc(session, model, doc_id: int):
    doc = session.get(model, doc_id)
    if doc is None:
        raise DomainError("not_found", "Документ не найден", status_code=404)
    return doc


def products_list(session):
    return [product_dict(p) for p in session.scalars(select(Product).order_by(Product.sku))]


def warehouses_list(session):
    return [warehouse_dict(w) for w in session.scalars(select(Warehouse).order_by(Warehouse.id))]


# ---------- склады ----------

def warehouse_create(session, name: str) -> Warehouse:
    name = name.strip()
    if not name:
        raise DomainError("invalid_name", "Название ФФ не может быть пустым", status_code=400)
    shop = default_shop(session)
    dup = session.scalar(select(Warehouse).where(Warehouse.shop_id == shop.id,
                                                 Warehouse.name == name,
                                                 Warehouse.archived_at.is_(None)))
    if dup:
        raise DomainError("duplicate_name", f"Активный ФФ «{name}» уже существует")
    w = Warehouse(shop_id=shop.id, name=name)
    session.add(w)
    session.flush()
    post(session, op_type="WAREHOUSE_CREATE", comment=f"Создан ФФ «{name}»")
    return w


def warehouse_rename(session, w: Warehouse, name: str) -> Warehouse:
    name = name.strip()
    if not name:
        raise DomainError("invalid_name", "Название ФФ не может быть пустым", status_code=400)
    if w.archived_at:
        raise DomainError("warehouse_archived", "Архивный ФФ нельзя переименовать")
    dup = session.scalar(select(Warehouse).where(Warehouse.shop_id == w.shop_id,
                                                 Warehouse.name == name,
                                                 Warehouse.archived_at.is_(None),
                                                 Warehouse.id != w.id))
    if dup:
        raise DomainError("duplicate_name", f"Активный ФФ «{name}» уже существует")
    old = w.name
    w.name = name
    post(session, op_type="WAREHOUSE_RENAME", comment=f"«{old}» → «{name}»")
    return w


def warehouse_archive(session, w: Warehouse):
    """Удаление = архивация. Остатки остаются на складе, история не меняется.

    Новые документы на архивный ФФ запрещены, но уже созданные можно завершить
    (отправить/принять/отменить) — товар не «зависает» в пути.
    """
    if w.archived_at:
        return w, False
    w.archived_at = utcnow()
    post(session, op_type="WAREHOUSE_ARCHIVE",
         comment=f"ФФ «{w.name}» переведён в архив; остатки и история сохранены")
    return w, True


# ---------- создание документов ----------

def receipt_create(session, product_id: int, warehouse_id: int, qty: int) -> Operation:
    p = get_product(session, product_id)
    w = active_warehouse(session, warehouse_id)
    return post(session, op_type="RECEIPT", product_id=p.id, qty=qty,
                source="supplier", destination=ff(w.id), entries=[(ff(w.id), qty, 0)])


def transfer_create(session, product_id: int, from_id: int, to_id: int, qty: int) -> Transfer:
    p = get_product(session, product_id)
    src = active_warehouse(session, from_id)
    dst = active_warehouse(session, to_id)
    if from_id == to_id:
        raise DomainError("invalid_transfer", "ФФ отправления и назначения должны различаться",
                          status_code=400)
    ensure_available(session, src.id, p.id, qty)  # быстрая проверка; при отправке — повторная под блокировкой
    doc = Transfer(product_id=p.id, from_id=src.id, to_id=dst.id, qty=qty, status="NEW")
    session.add(doc)
    session.flush()
    return doc


def fbs_reserve(session, product_id: int, warehouse_id: int, qty: int) -> FbsOrder:
    p = get_product(session, product_id)
    w = active_warehouse(session, warehouse_id)
    doc = FbsOrder(product_id=p.id, warehouse_id=w.id, qty=qty, status="RESERVED")
    session.add(doc)
    session.flush()
    ensure_available(session, w.id, p.id, qty)
    post(session, op_type="FBS_RESERVE", product_id=p.id, qty=qty, doc_type="fbs", doc_id=doc.id,
         source=f"{ff(w.id)}:free", destination=f"{ff(w.id)}:reserved",
         entries=[(ff(w.id), 0, qty)])
    return doc


def fbo_create(session, product_id: int, warehouse_id: int, qty: int) -> FboShipment:
    p = get_product(session, product_id)
    w = active_warehouse(session, warehouse_id)
    ensure_available(session, w.id, p.id, qty)
    doc = FboShipment(product_id=p.id, warehouse_id=w.id, qty=qty, status="NEW")
    session.add(doc)
    session.flush()
    return doc


# ---------- идемпотентные переходы документов ----------

def _lock_doc(session, doc) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            select(type(doc)).where(type(doc).id == doc.id)
            .with_for_update().execution_options(populate_existing=True)
        )


def _apply(session, doc, *, action, src, dst, noop, op_type, doc_type, source, destination, entries):
    """Переход статуса документа. Повторное подтверждение — no-op (applied=False).

    Защита от двойного списания в три слоя:
    1) блокировка строки документа (FOR UPDATE на PostgreSQL);
    2) проверка статуса: переход выполняется только из исходного статуса;
    3) UNIQUE(doc_type, doc_id, type) в журнале — даже при гонке проводки
       создаются ровно один раз.
    """
    _lock_doc(session, doc)
    if doc.status in noop:
        return doc, False
    if doc.status != src:
        raise DomainError("invalid_state",
                          f"«{action}» недоступно: документ в статусе "
                          f"«{STATUS_LABELS.get(doc.status, doc.status)}».",
                          status=doc.status)
    try:
        with session.begin_nested():
            post(session, op_type=op_type, product_id=doc.product_id, qty=doc.qty,
                 doc_type=doc_type, doc_id=doc.id, source=source, destination=destination,
                 entries=entries(session, doc))
            doc.status = dst
            doc.updated_at = utcnow()
            session.flush()
    except IntegrityError:
        # Гонка двух подтверждений: уникальный индекс журнала не дал списать дважды.
        session.refresh(doc)
        if doc.status in noop:
            return doc, False
        raise
    return doc, True


def transfer_ship(session, doc):
    def entries(s, d):
        ensure_available(s, d.from_id, d.product_id, d.qty)
        return [(ff(d.from_id), -d.qty, 0), (transfer_loc(d.id), d.qty, 0)]
    return _apply(session, doc, action="Отправить", src="NEW", dst="SHIPPED",
                  noop={"SHIPPED", "RECEIVED"}, op_type="TRANSFER_SHIP", doc_type="transfer",
                  source=ff(doc.from_id), destination=transfer_loc(doc.id), entries=entries)


def transfer_receive(session, doc):
    def entries(s, d):
        return [(transfer_loc(d.id), -d.qty, 0), (ff(d.to_id), d.qty, 0)]
    return _apply(session, doc, action="Принять", src="SHIPPED", dst="RECEIVED",
                  noop={"RECEIVED"}, op_type="TRANSFER_RECEIVE", doc_type="transfer",
                  source=transfer_loc(doc.id), destination=ff(doc.to_id), entries=entries)


def transfer_cancel(session, doc):
    return _apply(session, doc, action="Отменить", src="NEW", dst="CANCELLED",
                  noop={"CANCELLED"}, op_type="TRANSFER_CANCEL", doc_type="transfer",
                  source=ff(doc.from_id), destination=None, entries=lambda s, d: [])


def fbs_ship(session, doc):
    def entries(s, d):
        # Товар уже зарезервирован: списываем qty и резерв одновременно.
        return [(ff(d.warehouse_id), -d.qty, -d.qty), (fbs_loc(d.id), d.qty, 0)]
    return _apply(session, doc, action="Отправить клиенту", src="RESERVED", dst="SHIPPED",
                  noop={"SHIPPED", "DELIVERED"}, op_type="FBS_SHIP", doc_type="fbs",
                  source=f"{ff(doc.warehouse_id)}:reserved", destination=fbs_loc(doc.id),
                  entries=entries)


def fbs_deliver(session, doc):
    def entries(s, d):
        return [(fbs_loc(d.id), -d.qty, 0)]
    return _apply(session, doc, action="Доставлен", src="SHIPPED", dst="DELIVERED",
                  noop={"DELIVERED"}, op_type="FBS_DELIVER", doc_type="fbs",
                  source=fbs_loc(doc.id), destination="customer", entries=entries)


def fbs_cancel(session, doc):
    def entries(s, d):
        return [(ff(d.warehouse_id), 0, -d.qty)]
    return _apply(session, doc, action="Отменить резерв", src="RESERVED", dst="CANCELLED",
                  noop={"CANCELLED"}, op_type="FBS_CANCEL", doc_type="fbs",
                  source=f"{ff(doc.warehouse_id)}:reserved", destination=f"{ff(doc.warehouse_id)}:free",
                  entries=entries)


def fbo_ship(session, doc):
    def entries(s, d):
        ensure_available(s, d.warehouse_id, d.product_id, d.qty)
        return [(ff(d.warehouse_id), -d.qty, 0), (fbo_loc(d.id), d.qty, 0)]
    return _apply(session, doc, action="Отправить", src="NEW", dst="SHIPPED",
                  noop={"SHIPPED", "ACCEPTED"}, op_type="FBO_SHIP", doc_type="fbo",
                  source=ff(doc.warehouse_id), destination=fbo_loc(doc.id), entries=entries)


def fbo_accept(session, doc):
    def entries(s, d):
        return [(fbo_loc(d.id), -d.qty, 0), (MARKETPLACE, d.qty, 0)]
    return _apply(session, doc, action="Принять на МП", src="SHIPPED", dst="ACCEPTED",
                  noop={"ACCEPTED"}, op_type="FBO_ACCEPT", doc_type="fbo",
                  source=fbo_loc(doc.id), destination=MARKETPLACE, entries=entries)


def fbo_cancel(session, doc):
    return _apply(session, doc, action="Отменить", src="NEW", dst="CANCELLED",
                  noop={"CANCELLED"}, op_type="FBO_CANCEL", doc_type="fbo",
                  source=ff(doc.warehouse_id), destination=None, entries=lambda s, d: [])


# ---------- сериализация ----------

def wh_label(w: Warehouse) -> str:
    return w.name + (" (архив)" if w.archived_at else "")


def warehouse_dict(w):
    return {"id": w.id, "name": w.name, "archived": bool(w.archived_at)}


def product_dict(p):
    return {"id": p.id, "sku": p.sku, "name": p.name}


def _names(session):
    ws = {w.id: w for w in session.scalars(select(Warehouse))}
    ps = {p.id: p for p in session.scalars(select(Product))}
    return ws, ps


def _transfer_dict(d, ws, ps):
    p = ps[d.product_id]
    return {"id": d.id, "kind": "transfer", "product_id": p.id, "sku": p.sku, "product": p.name,
            "qty": d.qty, "status": d.status, "status_label": STATUS_LABELS.get(d.status, d.status),
            "from_id": d.from_id, "from_name": wh_label(ws[d.from_id]),
            "to_id": d.to_id, "to_name": wh_label(ws[d.to_id]),
            "created_at": d.created_at.isoformat(), "updated_at": d.updated_at.isoformat()}


def _order_dict(d, kind, ws, ps):
    p = ps[d.product_id]
    return {"id": d.id, "kind": kind, "product_id": p.id, "sku": p.sku, "product": p.name,
            "qty": d.qty, "status": d.status, "status_label": STATUS_LABELS.get(d.status, d.status),
            "warehouse_id": d.warehouse_id, "warehouse_name": wh_label(ws[d.warehouse_id]),
            "created_at": d.created_at.isoformat(), "updated_at": d.updated_at.isoformat()}


def transfer_dict(session, d):
    ws, ps = _names(session)
    return _transfer_dict(d, ws, ps)


def fbs_dict(session, d):
    ws, ps = _names(session)
    return _order_dict(d, "fbs", ws, ps)


def fbo_dict(session, d):
    ws, ps = _names(session)
    return _order_dict(d, "fbo", ws, ps)


def location_label(code, ws):
    if not code:
        return "—"
    if code == "supplier":
        return "Поставщик"
    if code == "customer":
        return "Клиент"
    if code == MARKETPLACE:
        return "Склад маркетплейса"
    kind, _, rest = code.partition(":")
    if kind == "ff":
        wid, _, sub = rest.partition(":")
        w = ws.get(int(wid))
        base = wh_label(w) if w else f"ФФ #{wid}"
        if sub == "reserved":
            return f"Резерв FBS · {base}"
        if sub == "free":
            return f"Доступно · {base}"
        return base
    if kind == "transfer":
        return f"В пути между ФФ · перемещение #{rest}"
    if kind == "fbo":
        return f"В пути FBO · отгрузка #{rest}"
    if kind == "fbs":
        return f"В пути к клиенту · заказ #{rest}"
    return code


# ---------- витрины ----------

def stock_overview(session):
    ws, ps = _names(session)
    warehouses = sorted(ws.values(), key=lambda w: w.id)
    products = sorted(ps.values(), key=lambda p: p.sku)
    bal = {(b.location, b.product_id): b for b in session.scalars(select(Balance))}
    delivered = dict(session.execute(
        select(Operation.product_id, func.coalesce(func.sum(Operation.qty), 0))
        .where(Operation.type == "FBS_DELIVER").group_by(Operation.product_id)).all())

    rows = []
    for p in products:
        cells = []
        for w in warehouses:
            b = bal.get((ff(w.id), p.id))
            on_hand = b.qty if b else 0
            reserved = b.reserved if b else 0
            cells.append({"warehouse_id": w.id, "warehouse": w.name, "archived": bool(w.archived_at),
                          "on_hand": on_hand, "reserved": reserved, "available": on_hand - reserved})

        def transit_sum(prefix):
            return sum(b.qty for (loc, pid), b in bal.items()
                       if pid == p.id and loc.startswith(prefix))

        mp = bal.get((MARKETPLACE, p.id))
        mp_qty = mp.qty if mp else 0
        on_hand_total = sum(c["on_hand"] for c in cells)
        tt, tf, tc = transit_sum("transfer:"), transit_sum("fbo:"), transit_sum("fbs:")
        rows.append({
            "product_id": p.id, "sku": p.sku, "product": p.name, "by_warehouse": cells,
            "ff_on_hand": on_hand_total, "ff_reserved": sum(c["reserved"] for c in cells),
            "ff_available_active": sum(c["available"] for c in cells if not c["archived"]),
            "in_transit_transfer": tt, "in_transit_fbo": tf, "in_transit_fbs": tc,
            "marketplace": mp_qty, "delivered": delivered.get(p.id, 0),
            "total_accounted": on_hand_total + tt + tf + tc + mp_qty,
        })

    transit = {
        "transfers": [_transfer_dict(d, ws, ps) for d in session.scalars(
            select(Transfer).where(Transfer.status == "SHIPPED").order_by(Transfer.id))],
        "fbo": [_order_dict(d, "fbo", ws, ps) for d in session.scalars(
            select(FboShipment).where(FboShipment.status == "SHIPPED").order_by(FboShipment.id))],
        "fbs": [_order_dict(d, "fbs", ws, ps) for d in session.scalars(
            select(FbsOrder).where(FbsOrder.status == "SHIPPED").order_by(FbsOrder.id))],
    }
    return {"warehouses": [warehouse_dict(w) for w in warehouses], "rows": rows, "transit": transit}


def documents_list(session):
    ws, ps = _names(session)
    return {
        "transfers": [_transfer_dict(d, ws, ps) for d in session.scalars(
            select(Transfer).order_by(Transfer.id.desc()))],
        "fbs": [_order_dict(d, "fbs", ws, ps) for d in session.scalars(
            select(FbsOrder).order_by(FbsOrder.id.desc()))],
        "fbo": [_order_dict(d, "fbo", ws, ps) for d in session.scalars(
            select(FboShipment).order_by(FboShipment.id.desc()))],
    }


def operations_list(session, limit: int = 200):
    ws, ps = _names(session)
    ops = session.scalars(select(Operation).order_by(Operation.id.desc()).limit(limit)).all()
    out = []
    for o in ops:
        p = ps.get(o.product_id)
        out.append({"id": o.id, "type": o.type, "label": OP_LABELS.get(o.type, o.type),
                    "sku": p.sku if p else None, "product": p.name if p else None,
                    "qty": o.qty, "source": location_label(o.source, ws),
                    "destination": location_label(o.destination, ws), "comment": o.comment,
                    "doc": f"{o.doc_type}#{o.doc_id}" if o.doc_type else None,
                    "created_at": o.created_at.isoformat()})
    return out


def reconciliation(session):
    """Сверка: пересчёт остатков из журнала + закон сохранения по каждому SKU.

    Закон сохранения: сумма остатков всех внутренних локаций = приход − доставлено клиенту.
    """
    ws, ps = _names(session)
    expected = {}
    for pt in session.scalars(select(Posting)):
        e = expected.setdefault((pt.location, pt.product_id), [0, 0])
        e[0] += pt.qty_delta
        e[1] += pt.reserved_delta
    actual = {(b.location, b.product_id): [b.qty, b.reserved]
              for b in session.scalars(select(Balance))}

    discrepancies = []
    for key in sorted(set(expected) | set(actual)):
        e, a = expected.get(key, [0, 0]), actual.get(key, [0, 0])
        if e != a:
            discrepancies.append({"location": location_label(key[0], ws), "sku": ps[key[1]].sku,
                                  "ledger": {"qty": e[0], "reserved": e[1]},
                                  "balance": {"qty": a[0], "reserved": a[1]}})

    def sums(op_type):
        return dict(session.execute(
            select(Operation.product_id, func.coalesce(func.sum(Operation.qty), 0))
            .where(Operation.type == op_type).group_by(Operation.product_id)).all())

    receipts, delivered = sums("RECEIPT"), sums("FBS_DELIVER")
    conservation = []
    for p in sorted(ps.values(), key=lambda x: x.sku):
        total = sum(v[0] for (loc, pid), v in actual.items() if pid == p.id)
        exp = receipts.get(p.id, 0) - delivered.get(p.id, 0)
        conservation.append({"sku": p.sku, "receipts": receipts.get(p.id, 0),
                             "delivered": delivered.get(p.id, 0),
                             "expected_total": exp, "actual_total": total, "ok": exp == total})
    ok = not discrepancies and all(c["ok"] for c in conservation)
    return {"ok": ok, "locations_checked": len(set(expected) | set(actual)),
            "discrepancies": discrepancies, "conservation": conservation}
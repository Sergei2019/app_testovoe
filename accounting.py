"""Учётное ядро: локации, проводки, блокировки, инварианты, обработка нехватки."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from models import Balance, Operation, Posting, Warehouse

# Внешние «supplier» и «customer» в balances не хранятся:
# приход увеличивает сумму в системе, доставка клиенту — уменьшает.
MARKETPLACE = "marketplace"


def ff(warehouse_id: int) -> str:
    return f"ff:{warehouse_id}"


def transfer_loc(doc_id: int) -> str:
    return f"transfer:{doc_id}"


def fbo_loc(doc_id: int) -> str:
    return f"fbo:{doc_id}"


def fbs_loc(doc_id: int) -> str:
    return f"fbs:{doc_id}"


class DomainError(Exception):
    """Доменная ошибка -> структурированный HTTP-ответ (409/400/404)."""

    def __init__(self, code: str, message: str, status_code: int = 409, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def _is_pg(session) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def locked_balance(session, location: str, product_id: int) -> Balance:
    """Баланс под блокировкой строки (FOR UPDATE на PostgreSQL)."""
    stmt = select(Balance).where(Balance.location == location, Balance.product_id == product_id)
    if _is_pg(session):
        stmt = stmt.with_for_update()
    bal = session.execute(stmt).scalar_one_or_none()
    if bal is None:
        try:
            with session.begin_nested():
                bal = Balance(location=location, product_id=product_id, qty=0, reserved=0)
                session.add(bal)
                session.flush()
        except IntegrityError:
            # Гонка первой вставки на PostgreSQL: строку уже создал сосед — перечитываем.
            bal = session.execute(stmt).scalar_one()
    return bal


def post(session, *, op_type, entries=(), product_id=None, qty=None, source=None,
         destination=None, doc_type=None, doc_id=None, comment=None) -> Operation:
    """Одна операция = заголовок журнала + проводки + дельты балансов (в одной транзакции).

    Локации блокируются в отсортированном порядке — единый порядок захвата
    блокировок во всех операциях защищает от дедлоков.
    """
    op = Operation(type=op_type, product_id=product_id, qty=qty, source=source,
                   destination=destination, doc_type=doc_type, doc_id=doc_id, comment=comment)
    session.add(op)
    session.flush()
    for location, dq, dr in sorted(entries, key=lambda e: e[0]):
        bal = locked_balance(session, location, product_id)
        new_qty, new_res = bal.qty + dq, bal.reserved + dr
        if new_qty < 0 or new_res < 0 or new_res > new_qty:
            raise DomainError("balance_conflict", "Операция нарушает инвариант остатков",
                              location=location, qty=new_qty, reserved=new_res)
        bal.qty, bal.reserved = new_qty, new_res
        session.add(Posting(operation_id=op.id, product_id=product_id,
                            location=location, qty_delta=dq, reserved_delta=dr))
    session.flush()
    return op


def ensure_available(session, warehouse_id: int, product_id: int, requested: int) -> None:
    """Списывать можно только available = qty - reserved. При нехватке — 409 с подсказкой."""
    bal = locked_balance(session, ff(warehouse_id), product_id)
    available = bal.qty - bal.reserved
    if available >= requested:
        return

    warehouses = session.scalars(select(Warehouse).order_by(Warehouse.id)).all()
    breakdown, total_available = [], 0
    for w in warehouses:
        b = session.execute(
            select(Balance).where(Balance.location == ff(w.id), Balance.product_id == product_id)
        ).scalar_one_or_none()
        on_hand = b.qty if b else 0
        reserved = b.reserved if b else 0
        av = on_hand - reserved
        if not w.archived_at:
            total_available += av
        breakdown.append({"warehouse_id": w.id, "warehouse": w.name, "archived": bool(w.archived_at),
                          "on_hand": on_hand, "reserved": reserved, "available": av})

    current = next(x for x in breakdown if x["warehouse_id"] == warehouse_id)
    shortage = requested - available
    donors = sorted(
        [x for x in breakdown if x["warehouse_id"] != warehouse_id and not x["archived"] and x["available"] > 0],
        key=lambda x: -x["available"],
    )
    if total_available >= requested and donors:
        best = donors[0]
        move = min(shortage, best["available"])
        hint = (f"Не хватает {shortage} шт. Оформите перемещение «{best['warehouse']}» → "
                f"«{current['warehouse']}» на {move} шт. или уменьшите количество до {available}.")
    else:
        hint = (f"Общий доступный остаток по активным ФФ — {total_available} шт. "
                f"Оформите приход или уменьшите количество.")
    raise DomainError(
        "insufficient_stock",
        f"На ФФ «{current['warehouse']}» доступно {available} шт., запрошено {requested}.",
        requested=requested, warehouse_id=warehouse_id, warehouse=current["warehouse"],
        available_at_warehouse=available, shortage=shortage, total_available=total_available,
        by_warehouse=breakdown, hint=hint,
    )
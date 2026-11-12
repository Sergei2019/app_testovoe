"""HTTP API (FastAPI). Тонкий слой: валидация входа -> сервис -> JSON."""
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

import services as svc
from models import FboShipment, FbsOrder, Transfer


def get_session(request: Request):
    """Сессия на запрос: коммит при успехе, откат при любом исключении."""
    with request.app.state.session_factory() as session:
        yield session
        session.commit()


router = APIRouter(prefix="/api")


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ReceiptIn(BaseModel):
    product_id: int
    warehouse_id: int
    qty: int = Field(ge=1)


class TransferIn(BaseModel):
    product_id: int
    from_id: int
    to_id: int
    qty: int = Field(ge=1)


class OrderIn(BaseModel):
    product_id: int
    warehouse_id: int
    qty: int = Field(ge=1)


@router.get("/products")
def products(session=Depends(get_session)):
    return svc.products_list(session)


@router.get("/warehouses")
def warehouses(session=Depends(get_session)):
    return svc.warehouses_list(session)


@router.get("/stock")
def stock(session=Depends(get_session)):
    return svc.stock_overview(session)


@router.get("/documents")
def documents(session=Depends(get_session)):
    return svc.documents_list(session)


@router.get("/operations")
def operations(limit: int = Query(default=200, ge=1, le=1000), session=Depends(get_session)):
    return svc.operations_list(session, limit)


@router.get("/reconciliation")
def reconciliation(session=Depends(get_session)):
    return svc.reconciliation(session)


@router.post("/warehouses", status_code=201)
def warehouse_create(body: NameIn, session=Depends(get_session)):
    return svc.warehouse_dict(svc.warehouse_create(session, body.name))


@router.patch("/warehouses/{warehouse_id}")
def warehouse_rename(warehouse_id: int, body: NameIn, session=Depends(get_session)):
    w = svc.warehouse_rename(session, svc.get_warehouse(session, warehouse_id), body.name)
    return svc.warehouse_dict(w)


@router.delete("/warehouses/{warehouse_id}")
def warehouse_archive(warehouse_id: int, session=Depends(get_session)):
    w, applied = svc.warehouse_archive(session, svc.get_warehouse(session, warehouse_id))
    return {"applied": applied, "warehouse": svc.warehouse_dict(w)}


@router.post("/receipts", status_code=201)
def receipt_create(body: ReceiptIn, session=Depends(get_session)):
    op = svc.receipt_create(session, body.product_id, body.warehouse_id, body.qty)
    return {"operation_id": op.id}


@router.post("/transfers", status_code=201)
def transfer_create(body: TransferIn, session=Depends(get_session)):
    doc = svc.transfer_create(session, body.product_id, body.from_id, body.to_id, body.qty)
    return {"document": svc.transfer_dict(session, doc)}


@router.post("/transfers/{doc_id}/ship")
def transfer_ship(doc_id: int, session=Depends(get_session)):
    doc, applied = svc.transfer_ship(session, svc.get_doc(session, Transfer, doc_id))
    return {"applied": applied, "document": svc.transfer_dict(session, doc)}


@router.post("/transfers/{doc_id}/receive")
def transfer_receive(doc_id: int, session=Depends(get_session)):
    doc, applied = svc.transfer_receive(session, svc.get_doc(session, Transfer, doc_id))
    return {"applied": applied, "document": svc.transfer_dict(session, doc)}


@router.post("/transfers/{doc_id}/cancel")
def transfer_cancel(doc_id: int, session=Depends(get_session)):
    doc, applied = svc.transfer_cancel(session, svc.get_doc(session, Transfer, doc_id))
    return {"applied": applied, "document": svc.transfer_dict(session, doc)}


@router.post("/fbs-orders", status_code=201)
def fbs_reserve(body: OrderIn, session=Depends(get_session)):
    doc = svc.fbs_reserve(session, body.product_id, body.warehouse_id, body.qty)
    return {"document": svc.fbs_dict(session, doc)}


@router.post("/fbs-orders/{doc_id}/ship")
def fbs_ship(doc_id: int, session=Depends(get_session)):
    doc, applied = svc.fbs_ship(session, svc.get_doc(session, FbsOrder, doc_id))
    return {"applied": applied, "document": svc.fbs_dict(session, doc)}


@router.post("/fbs-orders/{doc_id}/deliver")
def fbs_deliver(doc_id: int, session=Depends(get_session)):
    doc, applied = svc.fbs_deliver(session, svc.get_doc(session, FbsOrder, doc_id))
    return {"applied": applied, "document": svc.fbs_dict(session, doc)}


@router.post("/fbs-orders/{doc_id}/cancel")
def fbs_cancel(doc_id: int, session=Depends(get_session)):
    doc, applied = svc.fbs_cancel(session, svc.get_doc(session, FbsOrder, doc_id))
    return {"applied": applied, "document": svc.fbs_dict(session, doc)}


@router.post("/fbo-shipments", status_code=201)
def fbo_create(body: OrderIn, session=Depends(get_session)):
    doc = svc.fbo_create(session, body.product_id, body.warehouse_id, body.qty)
    return {"document": svc.fbo_dict(session, doc)}


@router.post("/fbo-shipments/{doc_id}/ship")
def fbo_ship(doc_id: int, session=Depends(get_session)):
    doc, applied = svc.fbo_ship(session, svc.get_doc(session, FboShipment, doc_id))
    return {"applied": applied, "document": svc.fbo_dict(session, doc)}


@router.post("/fbo-shipments/{doc_id}/accept")
def fbo_accept(doc_id: int, session=Depends(get_session)):
    doc, applied = svc.fbo_accept(session, svc.get_doc(session, FboShipment, doc_id))
    return {"applied": applied, "document": svc.fbo_dict(session, doc)}


@router.post("/fbo-shipments/{doc_id}/cancel")
def fbo_cancel(doc_id: int, session=Depends(get_session)):
    doc, applied = svc.fbo_cancel(session, svc.get_doc(session, FboShipment, doc_id))
    return {"applied": applied, "document": svc.fbo_dict(session, doc)}
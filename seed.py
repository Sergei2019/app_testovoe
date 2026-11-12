"""Тестовые данные из задания: один магазин, ФФ «Север» и «Юг», три артикула."""
from sqlalchemy import select

import services as svc
from models import Product, Shop, Warehouse


def seed(session) -> None:
    if session.scalar(select(Shop).limit(1)) is not None:
        return
    shop = Shop(name="Мой магазин")
    session.add(shop)
    session.flush()
    north = Warehouse(shop_id=shop.id, name="Север")
    south = Warehouse(shop_id=shop.id, name="Юг")
    session.add_all([north, south])
    session.flush()

    items = [
        ("SKU-101", "Органайзер", {north.id: 10, south.id: 6}),
        ("SKU-102", "Контейнер", {north.id: 5}),
        ("SKU-103", "Корзина", {south.id: 4}),
    ]
    for sku, name, quantities in items:
        p = Product(shop_id=shop.id, sku=sku, name=name)
        session.add(p)
        session.flush()
        for warehouse_id, qty in quantities.items():
            svc.receipt_create(session, p.id, warehouse_id, qty)
    session.commit()
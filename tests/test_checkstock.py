"""Складские сценарии из задания: учёт, нехватка, идемпотентность, архивация ФФ, сверка."""
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

PG = os.getenv("TEST_DATABASE_URL", "").startswith("postgresql")


def ok(resp, code=200):
    assert resp.status_code == code, resp.text
    return resp.json()


def row(client, sku):
    return next(r for r in client.get("/api/stock").json()["rows"] if r["sku"] == sku)


def cell(client, sku, warehouse):
    return next(c for c in row(client, sku)["by_warehouse"] if c["warehouse"] == warehouse)


def recon_ok(client):
    assert ok(client.get("/api/reconciliation"))["ok"] is True


def test_seed_balances(client):
    c = cell(client, "SKU-101", "Север")
    assert (c["on_hand"], c["reserved"], c["available"]) == (10, 0, 10)
    assert cell(client, "SKU-101", "Юг")["on_hand"] == 6
    assert cell(client, "SKU-102", "Север")["on_hand"] == 5
    assert cell(client, "SKU-102", "Юг")["on_hand"] == 0
    assert cell(client, "SKU-103", "Юг")["on_hand"] == 4
    assert row(client, "SKU-101")["ff_on_hand"] == 16
    recon_ok(client)


def test_receipt(client, ids):
    p, w = ids
    ok(client.post("/api/receipts",
                   json={"product_id": p["SKU-102"], "warehouse_id": w["Юг"], "qty": 7}), 201)
    assert cell(client, "SKU-102", "Юг")["on_hand"] == 7
    ops = ok(client.get("/api/operations"))
    assert ops[0]["type"] == "RECEIPT" and ops[0]["qty"] == 7
    recon_ok(client)


def test_receipt_invalid_qty(client, ids):
    p, w = ids
    r = client.post("/api/receipts",
                    json={"product_id": p["SKU-101"], "warehouse_id": w["Север"], "qty": 0})
    assert r.status_code == 422


def test_reserve_blocks_and_cancel(client, ids):
    p, w = ids
    doc = ok(client.post("/api/fbs-orders",
                         json={"product_id": p["SKU-101"], "warehouse_id": w["Север"], "qty": 4}),
             201)["document"]
    c = cell(client, "SKU-101", "Север")
    assert (c["on_hand"], c["reserved"], c["available"]) == (10, 4, 6)
    ok(client.post(f"/api/fbs-orders/{doc['id']}/cancel"))
    c = cell(client, "SKU-101", "Север")
    assert (c["on_hand"], c["reserved"], c["available"]) == (10, 0, 10)
    recon_ok(client)


def test_reserve_shortage_details(client, ids):
    """Нехватка на выбранном ФФ при достаточном общем остатке: 409 + разбивка + подсказка."""
    p, w = ids
    r = client.post("/api/fbs-orders",
                    json={"product_id": p["SKU-101"], "warehouse_id": w["Юг"], "qty": 8})
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "insufficient_stock"
    d = body["details"]
    assert d["available_at_warehouse"] == 6
    assert d["total_available"] == 16
    assert d["shortage"] == 2
    assert "перемещение" in d["hint"]
    assert cell(client, "SKU-101", "Юг")["reserved"] == 0
    assert ok(client.get("/api/documents"))["fbs"] == []
    recon_ok(client)


def test_shortage_resolved_by_transfer(client, ids):
    p, w = ids
    t = ok(client.post("/api/transfers",
                       json={"product_id": p["SKU-101"], "from_id": w["Север"],
                             "to_id": w["Юг"], "qty": 2}), 201)["document"]
    ok(client.post(f"/api/transfers/{t['id']}/ship"))
    ok(client.post(f"/api/transfers/{t['id']}/receive"))
    ok(client.post("/api/fbs-orders",
                   json={"product_id": p["SKU-101"], "warehouse_id": w["Юг"], "qty": 8}), 201)
    c = cell(client, "SKU-101", "Юг")
    assert (c["on_hand"], c["reserved"], c["available"]) == (8, 8, 0)
    recon_ok(client)


def test_transfer_transit_and_history(client, ids):
    p, w = ids
    t = ok(client.post("/api/transfers",
                       json={"product_id": p["SKU-101"], "from_id": w["Север"],
                             "to_id": w["Юг"], "qty": 4}), 201)["document"]
    ok(client.post(f"/api/transfers/{t['id']}/ship"))
    assert cell(client, "SKU-101", "Север")["on_hand"] == 6
    assert row(client, "SKU-101")["in_transit_transfer"] == 4
    transit = ok(client.get("/api/stock"))["transit"]["transfers"]
    assert len(transit) == 1 and transit[0]["qty"] == 4
    ok(client.post(f"/api/transfers/{t['id']}/receive"))
    assert cell(client, "SKU-101", "Юг")["on_hand"] == 10
    assert row(client, "SKU-101")["in_transit_transfer"] == 0
    ops = ok(client.get("/api/operations"))
    ship = next(o for o in ops if o["type"] == "TRANSFER_SHIP")
    assert ship["qty"] == 4 and ship["source"] == "Север" and "В пути" in ship["destination"]
    assert any(o["type"] == "TRANSFER_RECEIVE" for o in ops)
    recon_ok(client)


def test_transfer_double_confirm_is_noop(client, ids):
    p, w = ids
    t = ok(client.post("/api/transfers",
                       json={"product_id": p["SKU-101"], "from_id": w["Север"],
                             "to_id": w["Юг"], "qty": 4}), 201)["document"]
    r1 = ok(client.post(f"/api/transfers/{t['id']}/ship"))
    r2 = ok(client.post(f"/api/transfers/{t['id']}/ship"))
    assert r1["applied"] is True and r2["applied"] is False
    assert cell(client, "SKU-101", "Север")["on_hand"] == 6
    r3 = ok(client.post(f"/api/transfers/{t['id']}/receive"))
    r4 = ok(client.post(f"/api/transfers/{t['id']}/receive"))
    assert r3["applied"] is True and r4["applied"] is False
    assert cell(client, "SKU-101", "Юг")["on_hand"] == 10
    ops = ok(client.get("/api/operations"))
    assert len([o for o in ops if o["doc"] == f"transfer#{t['id']}"]) == 2
    recon_ok(client)


def test_receive_before_ship_rejected(client, ids):
    p, w = ids
    t = ok(client.post("/api/transfers",
                       json={"product_id": p["SKU-101"], "from_id": w["Север"],
                             "to_id": w["Юг"], "qty": 1}), 201)["document"]
    r = client.post(f"/api/transfers/{t['id']}/receive")
    assert r.status_code == 409 and r.json()["error"] == "invalid_state"


def test_transfer_same_warehouse_rejected(client, ids):
    p, w = ids
    r = client.post("/api/transfers",
                    json={"product_id": p["SKU-101"], "from_id": w["Север"],
                          "to_id": w["Север"], "qty": 1})
    assert r.status_code == 400


def test_fbs_full_flow(client, ids):
    p, w = ids
    doc = ok(client.post("/api/fbs-orders",
                         json={"product_id": p["SKU-101"], "warehouse_id": w["Север"], "qty": 3}),
             201)["document"]
    ok(client.post(f"/api/fbs-orders/{doc['id']}/ship"))
    c = cell(client, "SKU-101", "Север")
    assert (c["on_hand"], c["reserved"], c["available"]) == (7, 0, 7)
    assert row(client, "SKU-101")["in_transit_fbs"] == 3
    ok(client.post(f"/api/fbs-orders/{doc['id']}/deliver"))
    r = row(client, "SKU-101")
    assert r["in_transit_fbs"] == 0 and r["delivered"] == 3 and r["total_accounted"] == 13
    recon_ok(client)


def test_fbs_cancel_after_ship_rejected(client, ids):
    p, w = ids
    doc = ok(client.post("/api/fbs-orders",
                         json={"product_id": p["SKU-101"], "warehouse_id": w["Север"], "qty": 1}),
             201)["document"]
    ok(client.post(f"/api/fbs-orders/{doc['id']}/ship"))
    r = client.post(f"/api/fbs-orders/{doc['id']}/cancel")
    assert r.status_code == 409 and r.json()["error"] == "invalid_state"


def test_fbo_flow_and_double_accept(client, ids):
    p, w = ids
    doc = ok(client.post("/api/fbo-shipments",
                         json={"product_id": p["SKU-102"], "warehouse_id": w["Север"], "qty": 5}),
             201)["document"]
    ok(client.post(f"/api/fbo-shipments/{doc['id']}/ship"))
    assert cell(client, "SKU-102", "Север")["on_hand"] == 0
    assert row(client, "SKU-102")["in_transit_fbo"] == 5
    r1 = ok(client.post(f"/api/fbo-shipments/{doc['id']}/accept"))
    r2 = ok(client.post(f"/api/fbo-shipments/{doc['id']}/accept"))
    assert r1["applied"] is True and r2["applied"] is False
    r = row(client, "SKU-102")
    assert r["marketplace"] == 5 and r["in_transit_fbo"] == 0
    recon_ok(client)


def test_fbo_ship_rechecks_available(client, ids):
    """Проверка доступности выполняется в момент списания, а не только при создании."""
    p, w = ids
    fbo = ok(client.post("/api/fbo-shipments",
                         json={"product_id": p["SKU-101"], "warehouse_id": w["Север"], "qty": 6}),
             201)["document"]
    ok(client.post("/api/fbs-orders",
                   json={"product_id": p["SKU-101"], "warehouse_id": w["Север"], "qty": 8}), 201)
    r = client.post(f"/api/fbo-shipments/{fbo['id']}/ship")
    assert r.status_code == 409
    assert r.json()["details"]["available_at_warehouse"] == 2
    recon_ok(client)


def test_archive_keeps_stock_history_and_blocks_new(client, ids):
    p, w = ids
    ops_before = len(ok(client.get("/api/operations")))
    r = ok(client.delete(f"/api/warehouses/{w['Юг']}"))
    assert r["applied"] is True and r["warehouse"]["archived"] is True
    c = cell(client, "SKU-103", "Юг")
    assert c["on_hand"] == 4 and c["archived"] is True
    assert len(ok(client.get("/api/operations"))) >= ops_before
    assert client.post("/api/receipts",
                       json={"product_id": p["SKU-103"], "warehouse_id": w["Юг"], "qty": 1}
                       ).status_code == 409
    assert client.post("/api/transfers",
                       json={"product_id": p["SKU-103"], "from_id": w["Юг"],
                             "to_id": w["Север"], "qty": 1}).status_code == 409
    assert client.post("/api/fbs-orders",
                       json={"product_id": p["SKU-103"], "warehouse_id": w["Юг"], "qty": 1}
                       ).status_code == 409
    r2 = ok(client.delete(f"/api/warehouses/{w['Юг']}"))
    assert r2["applied"] is False
    recon_ok(client)


def test_archive_allows_finishing_inflight(client, ids):
    """Начатые документы можно завершить даже после архивации ФФ — товар не зависает."""
    p, w = ids
    order = ok(client.post("/api/fbs-orders",
                           json={"product_id": p["SKU-101"], "warehouse_id": w["Юг"], "qty": 2}),
               201)["document"]
    t = ok(client.post("/api/transfers",
                       json={"product_id": p["SKU-101"], "from_id": w["Север"],
                             "to_id": w["Юг"], "qty": 3}), 201)["document"]
    ok(client.post(f"/api/transfers/{t['id']}/ship"))
    ok(client.delete(f"/api/warehouses/{w['Юг']}"))
    assert ok(client.post(f"/api/transfers/{t['id']}/receive"))["applied"] is True
    c = cell(client, "SKU-101", "Юг")
    assert (c["on_hand"], c["reserved"]) == (9, 2)
    assert ok(client.post(f"/api/fbs-orders/{order['id']}/ship"))["applied"] is True
    c = cell(client, "SKU-101", "Юг")
    assert (c["on_hand"], c["reserved"], c["available"]) == (7, 0, 7)
    assert row(client, "SKU-101")["in_transit_fbs"] == 2
    recon_ok(client)


def test_rename_and_duplicate_names(client, ids):
    p, w = ids
    ok(client.patch(f"/api/warehouses/{w['Север']}", json={"name": "Север-1"}))
    assert cell(client, "SKU-101", "Север-1")["on_hand"] == 10
    assert client.post("/api/warehouses", json={"name": "Юг"}).status_code == 409
    ok(client.delete(f"/api/warehouses/{w['Юг']}"))
    new = ok(client.post("/api/warehouses", json={"name": "Юг"}), 201)
    assert new["id"] != w["Юг"]
    recon_ok(client)


def test_mixed_scenario_conservation(client, ids):
    p, w = ids
    ok(client.post("/api/receipts",
                   json={"product_id": p["SKU-103"], "warehouse_id": w["Север"], "qty": 6}), 201)
    order = ok(client.post("/api/fbs-orders",
                           json={"product_id": p["SKU-101"], "warehouse_id": w["Север"], "qty": 3}),
               201)["document"]
    ok(client.post(f"/api/fbs-orders/{order['id']}/ship"))
    ok(client.post(f"/api/fbs-orders/{order['id']}/deliver"))
    t = ok(client.post("/api/transfers",
                       json={"product_id": p["SKU-102"], "from_id": w["Север"],
                             "to_id": w["Юг"], "qty": 2}), 201)["document"]
    ok(client.post(f"/api/transfers/{t['id']}/ship"))
    ok(client.post(f"/api/transfers/{t['id']}/receive"))
    fbo = ok(client.post("/api/fbo-shipments",
                         json={"product_id": p["SKU-101"], "warehouse_id": w["Юг"], "qty": 4}),
             201)["document"]
    ok(client.post(f"/api/fbo-shipments/{fbo['id']}/ship"))
    ok(client.post(f"/api/fbo-shipments/{fbo['id']}/accept"))
    rec = ok(client.get("/api/reconciliation"))
    assert rec["ok"] is True
    by_sku = {c["sku"]: c for c in rec["conservation"]}
    assert by_sku["SKU-101"]["expected_total"] == 13 and by_sku["SKU-101"]["actual_total"] == 13
    assert by_sku["SKU-102"]["actual_total"] == 5
    assert by_sku["SKU-103"]["actual_total"] == 10


@pytest.mark.skipif(not PG, reason="требуется PostgreSQL (TEST_DATABASE_URL)")
def test_concurrent_double_ship(client, ids):
    """Два одновременных подтверждения на PostgreSQL: списание ровно одно."""
    p, w = ids
    t = ok(client.post("/api/transfers",
                       json={"product_id": p["SKU-101"], "from_id": w["Север"],
                             "to_id": w["Юг"], "qty": 4}), 201)["document"]
    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(client.post, f"/api/transfers/{t['id']}/ship")
        f2 = ex.submit(client.post, f"/api/transfers/{t['id']}/ship")
        results = [f1.result(), f2.result()]
    assert all(r.status_code == 200 for r in results)
    assert sorted(r.json()["applied"] for r in results) == [False, True]
    assert cell(client, "SKU-101", "Север")["on_hand"] == 6
    recon_ok(client)
"""The FX snapshot must carry the review notice, with the real figures."""
from trading_algo import paper_trade as pt
from trading_algo.dashboard import fx_api
from trading_algo.forex import fx_book
from trading_algo.forex import fx_config as fxcfg


def test_the_fx_snapshot_states_the_books_are_under_review(tmp_path, monkeypatch):
    """Spec 15.2: the FX dashboard says plainly that these books back-test
    negative and are under review, with the numbers. One definition, so the
    terminal SPA and the published static page cannot drift apart."""
    monkeypatch.setattr(fx_book, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(pt, "STATE_DIR", str(tmp_path))
    fx_book.init_account("matt", 5_000.0, "balanced")

    snap = fx_api.build_fx_snapshot("matt")
    assert snap["review_notice"] == fxcfg.REVIEW_NOTICE
    assert "UNDER REVIEW" in fxcfg.REVIEW_NOTICE.upper()
    for sharpe in ("−2.06", "−2.14", "−5.56", "−6.25"):
        assert sharpe in fxcfg.REVIEW_NOTICE

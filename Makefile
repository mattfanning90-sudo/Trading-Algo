.PHONY: install test dashboard dashboard-app mac-app backtest sweep clean

install:
	pip install -r requirements.txt

test:
	pytest -q

# Web dashboard (browser) — synthetic demo on the "full" account
dashboard:
	python -m trading_algo.dashboard --account full --synthetic

# Native window straight from source (needs: pip install pywebview pyobjc-framework-WebKit)
dashboard-app:
	python -m trading_algo.dashboard.desktop --account full --synthetic

# Build the macOS .app bundle (run on a Mac)
mac-app:
	bash packaging/build_mac_app.sh

backtest:
	python -m trading_algo.run_backtest --synthetic

sweep:
	python -m trading_algo.sweep --region US --synthetic

clean:
	rm -rf build dist *.egg-info .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

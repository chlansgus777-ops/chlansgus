"""Point-in-time backtest of the score's predictive power (docs/backtest). Measurement only: the scoring formula,
weights and operating settings are never changed from here.

The backtest database is a SEPARATE SQLite file (never the operating one): the app's own tables (price bars,
splits, fundamental vintages, settings) plus the ``bt_*`` tables of :mod:`marketlens.backtest.schema`.
"""

# backend/app/safety/__init__.py
"""
Safety compliance layer (zone events, rules, decisions, man-down, occupancy).

Everything in this package is behind SAFETY_ENABLED (see config.py). With the
flag off, nothing here is constructed and the pipeline behaves exactly as it
did before the safety work started.
"""

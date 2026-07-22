"""SEP-1686 wire layer, moved intact and awaiting Phase 3 adaptation.

Every module in this subpackage is the original SEP-1686-shaped wire code:
the four CRUD request handlers (`requests.py`), the task-submission handler
(`handlers.py`), the Docket-subscription status relay (`subscriptions.py`), the
Redis push relay for elicitation (`elicitation.py`, `notifications.py`), the
capability declaration (`capabilities.py`), and the mode-routing dispatcher
(`routing.py`).

It is disconnected from core — nothing wires these handlers onto a server after
Phase 2. Phase 3 adapts this code in place to the SEP-2663 `tasks/get|update|cancel`
shape under its ported tests. Do not "improve" it here; the point of keeping it is
that it embodies operational lessons the rewrite must preserve.
"""

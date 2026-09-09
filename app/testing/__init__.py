"""Test-only helpers.

Nothing in this package is imported by the running application. It exists so
that pytest modules and the standalone verification scripts under
``backend/scripts`` share exactly one implementation of the rules for touching
a database during verification — most importantly the rule that says a shared
or company server is never a valid target.
"""

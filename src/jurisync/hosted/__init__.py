"""Hosted Jurisync: one remote MCP server for several offices, OAuth for the caller, a vault for portal credentials.

See docs/hosted.md. Connectors are unchanged: they read their account through ``jurisync.core.credentials``, which this
package fills per request from the caller's tenant.
"""

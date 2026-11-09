"""User authentication and role-based access control for the AURUM API (added with Module 07).

People sign in with a username and password and get a server-side session, carried by an httpOnly cookie, with a CSRF
token for state-changing requests. Other modules and n8n use service tokens (``Authorization: Bearer``). A service token
can never perform a human action such as approving a trade.

Passwords are stored as salted PBKDF2-SHA256 hashes. Session and service tokens are stored as SHA-256 hashes, so a database
copy cannot be used to sign in. No password, token or hash is ever logged or returned by the API.
"""

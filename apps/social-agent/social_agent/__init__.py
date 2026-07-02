"""quafel social agent — a small local app that gives quafel a witty X presence.

Design constraints (see README.md):
- DRY-RUN by default until X OAuth 1.0a user-context keys exist in .env.
- Every outgoing text passes a strong-judge quality gate (gpt-4o class) plus an
  exact + semantic uniqueness gate against ALL past posts/replies.
- All generated/planned/posted content is stored in the shared Azure Postgres
  database (tables: social_profiles, social_posts, social_bot_state).
"""

__version__ = "0.1.0"

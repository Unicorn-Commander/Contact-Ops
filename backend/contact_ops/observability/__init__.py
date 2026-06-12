"""App-level observability (error tracking + HTTP metrics).

Distinct from contact_ops.agents.observability (agent-fleet metrics/OTel). This
package holds the request-path observability: Sentry error tracking (dormant
until SENTRY_DSN is set) and the HTTP request metrics middleware.
"""

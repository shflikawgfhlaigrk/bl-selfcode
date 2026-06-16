# _shared_core — the spine every product imports (COPIES)

These are copies of the Utah core modules that the product areas import in common. Copied
**once** here instead of duplicating them into all six areas (which would drift). Source of
truth is `~/ProjectUtah/utah/`.

| Module | What a branch-off uses it for |
|--------|-------------------------------|
| `config.py` | env/secrets/paths, business identity, feature flags |
| `failures.py` | the failure ledger (record + gate) |
| `db_pool.py` | pooled Postgres connections |
| `foundation.py` | shared DB schema / bootstrap helpers |
| `alerts.py` | Pushover/Discord alert taxonomy |
| `mail.py` | real SMTP send + multi-account rotation (now bounce-pause-aware) |
| `mail_capacity.py` | warmup caps, deliverability, bounce auto-pause |
| `mail_replies.py` | IMAP reply/bounce detection |
| `sms.py` | SMS send path |
| `local_brain.py` | local LLM helper (marketing copy, etc.) |
| `objects.py` | shared data objects |

Still package-level deps (not single files — vendor from `utah/` as needed):
`utah/integrations/`, `utah/daemon/`.

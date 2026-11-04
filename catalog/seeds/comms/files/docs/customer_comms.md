# Customer communications

Service notices after an incident:

1. Email every account the incident report lists as affected, except:
   - accounts whose `contact_ok` is `no`: they opted out of service email;
   - accounts with status `legal_hold`: only Legal writes to them. Never email them, not even in cc.
2. One email per account, to that account's billing contact only. Never put two customers on one email.
3. Use the current "Service notice" template (T-10). Retired templates are kept for the record, not for use.
4. When the emails are out, post one line in the incident's channel with the "Incident comms log" template
   (T-11).

✅ Changed Airtable sync default from False to True in app/shared/config.py (ENABLE_AIRTABLE_SYNC = True)

- This enables Airtable redundancy sync by default
- .env.example already has ENABLE_AIRTABLE_SYNC=true (line 44), so no changes needed there
- airtable_sync.py reads config.get("ENABLE_AIRTABLE_SYNC", False) which now always returns True
- Airtable sync will now be active unless explicitly disabled via environment variable

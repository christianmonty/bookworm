# This project is to upload books to Ebay easily

Goals:
1. Will utilize Google cloud storage to store cover/copywright pages
2. Will use OpenAI call to ingest photos
3. Will build basic review UI to confirm the AI ingestion
4. Will store metadata for each book as a row in PostgreSQL DB in GCP
5. Will use Ebay API to pull info for each book
6. Will use Ebay API to list each book for sale (each row in DB)
7. Upon sales, will use Ebay API to track sales and update collection

Note: Maybe set up Alembic to track schema changes/migrations later...

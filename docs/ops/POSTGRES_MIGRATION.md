# Render PostgreSQL Deployment Guide

## Overview

This document describes how to deploy NSA Webservice with a managed PostgreSQL database on Render using the blueprint configuration.

## Render Blueprint Configuration

The `render.yaml` file is configured to:
1. Provision a PostgreSQL database (`nsa-webservice-db`)
2. Wire the connection string to the web service via `fromDatabase`
3. Automatically connect the application to PostgreSQL

**Important:** Free-tier Render Postgres databases expire after 90 days unless upgraded. See the warning in `render.yaml`.

## Deploy using Render Blueprint

### Step 1: Create the Blueprint Deployment
1. Navigate to: https://dashboard.render.com
2. Click **"New"** 
3. Select **"Blueprint"**
4. Connect your GitHub repository containing `render.yaml`
5. Select the branch (e.g., `main`)
6. Click **"Apply"**

### Step 2: Render Provisioning
Render will automatically:
- Detect the `render.yaml` blueprint file
- Create the PostgreSQL database `nsa-webservice-db` on the free tier
- Build and deploy the web service
- Inject the `DATABASE_URL` from the database into the web service using `fromDatabase`

**Deployment time:** Typically 2-5 minutes

### Step 3: Verify Database is Live

#### Check Render Dashboard:
1. Go to **Databases** tab
2. Verify `nsa-webservice-db` shows status: **"Available"**
3. Note the database details (name, plan, region)

#### Check Service Logs:
1. Go to **Services** tab
2. Select `food-adjudication-portal`
3. Click **"Logs"**
4. Look for application startup messages
5. Verify no warnings about `"DATABASE_URL not set - falling back to SQLite"`

#### Test Database Connectivity:
- Access your deployed service URL (e.g., `https://food-adjudication-portal.onrender.com`)
- Navigate to any page that uses the database:
  - `/sample/list` - List samples
  - `/adjudication` - List adjudications
  - `/inspection/list` - List inspections
- Attempt to create a new record in any module
- If records can be created and retrieved, PostgreSQL is working correctly

## Database Connection Details

The connection is automatically managed by Render:
- **Database Name:** `nsa_db` (as configured in `render.yaml`)
- **User:** `nsa_user` (as configured in `render.yaml`)
- **Connection String:** Automatically injected into `DATABASE_URL` environment variable
- **Access:** Only accessible from within the Render network (internal connection)

## Free-Tier Database Expiry

**CRITICAL REMINDER:** 
- Free-tier PostgreSQL databases on Render **expire after 90 days**
- You will receive email notifications before expiry
- Check expiry date: Dashboard → Databases → `nsa-webservice-db` → Settings
- **Action Required:** Upgrade to a paid plan (Starter: $7/month) before expiry to avoid permanent data loss

## Migration from Existing SQLite Data

If you have existing data in SQLite that needs to be migrated to the new PostgreSQL database:

1. **Before deploying the blueprint:**
   - Run the migration script locally against your existing SQLite database
   - Export data to a backup format

2. **After PostgreSQL is provisioned:**
   - Get the connection string from Render Dashboard
   - Run migration script with the Render DATABASE_URL
   
3. **Alternative:**
   - For small datasets, you can recreate records manually through the web interface
   - For large datasets, contact Render support for assistance

## Troubleshooting

### Database Not Available
- **Symptom:** Service fails to start, logs show connection errors
- **Check:** Databases tab → `nsa-webservice-db` status
- **Fix:** Wait for database to provision (may take several minutes)

### Fallback to SQLite Warning
- **Symptom:** Logs show `"DATABASE_URL not set - falling back to SQLite"`
- **Check:** Service environment variables in Render Dashboard
- **Fix:** Verify `render.yaml` has correct `fromDatabase` reference

### Connection String Issues
- **Symptom:** Authentication errors
- **Check:** Database exists and is accessible
- **Fix:** Render automatically manages credentials - no manual intervention needed

## Manual PostgreSQL Setup (Alternative)

If not using the blueprint:

1. **Create PostgreSQL database manually:**
   - Go to Render Dashboard → New → PostgreSQL
   - Select free tier
   - Name: `nsa-webservice-db`
   - Database: `nsa_db`
   - User: `nsa_user`

2. **Set DATABASE_URL:**
   - Go to web service → Environment
   - Add: `DATABASE_URL` = `<connection_string_from_database>`

3. **Deploy** the service

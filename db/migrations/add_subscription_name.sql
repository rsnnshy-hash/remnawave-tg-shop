-- Migration: Add subscription_name column to subscriptions table
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS subscription_name VARCHAR(255);

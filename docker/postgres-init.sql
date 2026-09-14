-- Demo-only credentials. Production roles are provisioned by infrastructure.
DO $$
BEGIN
    IF to_regrole('workcore_app') IS NULL THEN
        CREATE ROLE workcore_app LOGIN PASSWORD 'workcore_app';
    END IF;
    IF to_regrole('workcore_worker') IS NULL THEN
        CREATE ROLE workcore_worker LOGIN PASSWORD 'workcore_worker' BYPASSRLS;
    END IF;
END
$$;

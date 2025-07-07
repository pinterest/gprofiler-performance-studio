#!/bin/bash
# One-liner command to execute the optimization insert directly
# Usage: Run this from the directory containing the SQL file

echo "Executing optimization query insert..."

clickhouse-client \
    --host=clickhouse-host.com \
    --port=9000 \
    --user=default \
    --database=flamedb \
    --compression=1 \
    --multiquery < comprehensive_optimization_all_services_insert_fixed_v2.sql

echo "Query execution completed. Check the results with:"
echo "clickhouse-client --host=clickhouse-host.com --port=9000 --user=default --database=flamedb --compression=1 --query \"SELECT count(*) FROM flamedb.optimization_pattern_summary_local WHERE created_date >= today()\""

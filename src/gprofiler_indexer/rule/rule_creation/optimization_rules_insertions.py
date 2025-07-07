#!/usr/bin/env python3
"""
YAML to ClickHouse SQL Converter for Optimization Rules

Converts YAML optimization rules configuration to ClickHouse INSERT SQL statements.
Useful for generating SQL migration files or direct SQL execution.

Usage:
    python3 optimization_rules_insertions.py --config-file rules.yaml --output-file rules.sql
    python3 optimization_rules_insertions.py --config-file rules.yaml --cluster-mode > cluster_rules.sql
"""

import argparse
import sys
import yaml
from datetime import datetime
from pathlib import Path


def load_yaml_config(config_file):
    """Load and parse YAML configuration file."""
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        if not config or 'rules' not in config:
            raise ValueError("YAML file must contain 'rules' key with a list of rules")
        
        return config['rules']
    
    except yaml.YAMLError as e:
        print(f"❌ YAML parsing error: {e}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"❌ File not found: {config_file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error loading config: {e}", file=sys.stderr)
        sys.exit(1)


def validate_yaml_file(config_file):
    """Validate that the config file has proper YAML extension."""
    file_path = Path(config_file)
    if file_path.suffix.lower() not in ['.yaml', '.yml']:
        print(f"❌ Invalid file extension. Please use .yaml or .yml files. Got: {file_path.suffix}", file=sys.stderr)
        sys.exit(1)


def escape_sql_string(value):
    """Escape string values for SQL insertion."""
    if value is None:
        return "NULL"
    
    # Convert to string and escape single quotes
    str_value = str(value)
    escaped = str_value.replace("'", "''")
    return f"'{escaped}'"


def format_array_for_sql(arr):
    """Format array values for SQL insertion."""
    if not arr:
        return "[]"
    
    # Escape each string element
    escaped_items = [escape_sql_string(item)[1:-1] for item in arr]  # Remove outer quotes
    return "[" + ", ".join(f"'{item}'" for item in escaped_items) + "]"


def fill_rule_defaults(rule):
    """Fill default values for missing rule fields."""
    filled_rule = rule.copy()
    
    # Map YAML field names to database schema field names
    if 'name' in rule:
        filled_rule['rule_name'] = rule['name']
    if 'pattern' in rule:
        filled_rule['callstack_pattern'] = rule['pattern']
    if 'technology' in rule:
        filled_rule['technology_stack'] = rule['technology']
    if 'category' in rule:
        filled_rule['optimization_type'] = rule['category']  # YAML 'category' -> DB 'optimization_type' 
    if 'optimization_type' in rule:
        filled_rule['rule_category'] = rule['optimization_type']  # YAML 'optimization_type' -> DB 'rule_category'
    if 'recommendation' in rule:
        filled_rule['optimization_description'] = rule['recommendation']
    if 'documentation_url' in rule:
        filled_rule['documentation_links'] = [rule['documentation_url']] if rule['documentation_url'] else []
    
    # Set default values for missing fields
    defaults = {
        'rule_name': filled_rule.get('rule_name', 'Unnamed Rule'),
        'callstack_pattern': filled_rule.get('callstack_pattern', ''),
        'platform_pattern': '.*',  # Default to match all platforms
        'technology_stack': filled_rule.get('technology_stack', 'General'),
        'rule_category': filled_rule.get('rule_category', 'CPU'),
        'optimization_type': filled_rule.get('optimization_type', 'SOFTWARE'),
        'optimization_description': filled_rule.get('optimization_description', ''),
        'relative_optimization_efficiency_min': 0.0,
        'relative_optimization_efficiency_max': 0.0,
        'precision_score': 0.5,
        'accuracy_score': 0.5,
        'implementation_complexity': 'MEDIUM',
        'rule_source': 'EXPERIMENTAL',
        'rule_status': 'EXPERIMENTAL',
        'tags': [],
        'documentation_links': filled_rule.get('documentation_links', []),
        'created_by': 'yaml-to-sql-script'
    }
    
    # Fill missing fields with defaults
    for field, default_value in defaults.items():
        if field not in filled_rule or filled_rule[field] is None:
            filled_rule[field] = default_value
    
    return filled_rule


def validate_rule_fields(rule):
    """Validate that required fields are present and valid."""
    required_fields = ['rule_id', 'rule_name', 'callstack_pattern', 'optimization_type', 'description']
    
    # Check required fields
    for field in required_fields:
        if field not in rule or not rule[field]:
            raise ValueError(f"Missing required field '{field}' in rule: {rule.get('rule_id', 'unknown')}")
    
    # Validate enum fields
    valid_optimization_types = ['HARDWARE', 'SOFTWARE', 'UTILIZATION']
    valid_complexities = ['EASY', 'MEDIUM', 'COMPLEX', 'VERY_COMPLEX']
    valid_sources = ['COMMUNITY', 'PRIVATE', 'VERIFIED', 'EXPERIMENTAL']
    valid_statuses = ['ACTIVE', 'DEPRECATED', 'EXPERIMENTAL', 'DISABLED']
    
    if rule['optimization_type'] not in valid_optimization_types:
        raise ValueError(f"Invalid optimization_type '{rule['optimization_type']}'. Must be one of: {valid_optimization_types}")
    
    # Validate optional enum fields
    if 'implementation_complexity' in rule and rule['implementation_complexity'] not in valid_complexities:
        raise ValueError(f"Invalid implementation_complexity '{rule['implementation_complexity']}'. Must be one of: {valid_complexities}")
    
    if 'rule_source' in rule and rule['rule_source'] not in valid_sources:
        raise ValueError(f"Invalid rule_source '{rule['rule_source']}'. Must be one of: {valid_sources}")
    
    if 'rule_status' in rule and rule['rule_status'] not in valid_statuses:
        raise ValueError(f"Invalid rule_status '{rule['rule_status']}'. Must be one of: {valid_statuses}")


def generate_insert_statement(rule, table_name, database='flamedb'):
    """Generate ClickHouse INSERT statement for a single rule."""
    # Fill defaults and validate
    filled_rule = fill_rule_defaults(rule)
    validate_rule_fields(filled_rule)
    
    # Current timestamp
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    # Build INSERT statement matching the schema
    insert_sql = f"""INSERT INTO {database}.{table_name} (
    rule_id, rule_name, callstack_pattern, platform_pattern,
    technology_stack, rule_category, optimization_type,
    description, optimization_description,
    relative_optimization_efficiency_min, relative_optimization_efficiency_max,
    precision_score, accuracy_score, implementation_complexity,
    rule_source, rule_status, tags, documentation_links,
    created_date, updated_date, created_by
) VALUES (
    {escape_sql_string(filled_rule['rule_id'])},
    {escape_sql_string(filled_rule['rule_name'])},
    {escape_sql_string(filled_rule['callstack_pattern'])},
    {escape_sql_string(filled_rule['platform_pattern'])},
    {escape_sql_string(filled_rule['technology_stack'])},
    {escape_sql_string(filled_rule['rule_category'])},
    {escape_sql_string(filled_rule['optimization_type'])},
    {escape_sql_string(filled_rule['description'])},
    {escape_sql_string(filled_rule['optimization_description'])},
    {filled_rule['relative_optimization_efficiency_min']},
    {filled_rule['relative_optimization_efficiency_max']},
    {filled_rule['precision_score']},
    {filled_rule['accuracy_score']},
    {escape_sql_string(filled_rule['implementation_complexity'])},
    {escape_sql_string(filled_rule['rule_source'])},
    {escape_sql_string(filled_rule['rule_status'])},
    {format_array_for_sql(filled_rule['tags'])},
    {format_array_for_sql(filled_rule['documentation_links'])},
    '{current_time}',
    '{current_time}',
    {escape_sql_string(filled_rule['created_by'])}
);"""
    
    return insert_sql


def convert_yaml_to_sql(config_file, output_file=None, cluster_mode=False, database='flamedb', verbose=False):
    """Convert YAML config file to ClickHouse SQL INSERT statements."""
    
    # Validate file extension
    validate_yaml_file(config_file)
    
    # Load YAML configuration
    if verbose:
        print(f"📂 Loading YAML config from: {config_file}")
    
    rules = load_yaml_config(config_file)
    
    if not rules:
        print("⚠️  No rules found in configuration file", file=sys.stderr)
        return
    
    # Determine table name
    table_name = 'optimization_rules_local' if cluster_mode else 'optimization_rules'
    
    # Set default output file if not provided
    if output_file is None:
        output_file = 'optimization_rules_insertions.sql'
    
    # Generate SQL header
    sql_lines = [
        "-- ClickHouse Optimization Rules INSERT Statements",
        f"-- Generated from: {config_file}",
        f"-- Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"-- Database: {database}",
        f"-- Table: {table_name}",
        f"-- Cluster mode: {'enabled' if cluster_mode else 'disabled'}",
        f"-- Total rules: {len(rules)}",
        "",
        "-- Note: These statements only INSERT new rules.",
        "-- To prevent duplicates, check existing rule_ids first:",
        f"-- SELECT rule_id FROM {database}.{table_name};",
        "",
    ]
    
    # Generate INSERT statements
    if verbose:
        print(f"🔄 Converting {len(rules)} rules to SQL...")
    
    successful_conversions = 0
    failed_conversions = 0
    
    for i, rule in enumerate(rules, 1):
        try:
            if verbose:
                rule_id = rule.get('rule_id', f'rule-{i}')
                print(f"  [{i:2d}/{len(rules)}] Processing rule: {rule_id}")
            
            insert_sql = generate_insert_statement(rule, table_name, database)
            
            sql_lines.append(f"-- Rule {i}: {rule.get('rule_name', rule.get('name', 'Unnamed Rule'))}")
            sql_lines.append(insert_sql)
            sql_lines.append("")
            
            successful_conversions += 1
            
        except Exception as e:
            error_msg = f"-- ERROR: Failed to convert rule {i}: {e}"
            sql_lines.append(error_msg)
            sql_lines.append("")
            
            if verbose:
                print(f"  ❌ Error converting rule {i}: {e}")
            
            failed_conversions += 1
    
    # Add summary footer
    sql_lines.extend([
        "-- Conversion Summary:",
        f"-- ✅ Successfully converted: {successful_conversions} rules",
        f"-- ❌ Failed conversions: {failed_conversions} rules",
        f"-- 📊 Total processed: {len(rules)} rules",
        "",
        "-- To execute this SQL file:",
        f"-- clickhouse-client --database {database} --multiquery < {output_file}",
    ])
    
    # Join all SQL lines
    sql_content = "\n".join(sql_lines)
    
    # Write to output file
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(sql_content)
        
        print(f"✅ SQL statements written to: {output_file}")
        print(f"📊 Converted {successful_conversions} rules successfully")
        
        if failed_conversions > 0:
            print(f"⚠️  {failed_conversions} rules failed conversion (see comments in SQL file)")
    
    except Exception as e:
        print(f"❌ Error writing to file {output_file}: {e}", file=sys.stderr)
        sys.exit(1)
    
    return successful_conversions, failed_conversions


def main():
    parser = argparse.ArgumentParser(
        description="Convert YAML optimization rules to ClickHouse INSERT SQL statements",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert to default SQL file
  python3 optimization_rules_insertions.py --config-file rules.yaml
  
  # Convert to custom SQL file
  python3 optimization_rules_insertions.py --config-file rules.yaml --output-file custom_rules.sql
  
  # Generate cluster mode SQL  
  python3 optimization_rules_insertions.py --config-file rules.yaml --cluster-mode --output-file cluster_rules.sql
  
  # Enable verbose logging
  python3 optimization_rules_insertions.py --config-file rules.yaml --verbose
  
  # Custom database name
  python3 optimization_rules_insertions.py --config-file rules.yaml --database mydb --output-file custom_rules.sql
        """
    )
    
    parser.add_argument(
        '--config-file', 
        required=True, 
        help='YAML configuration file (.yaml or .yml)'
    )
    
    parser.add_argument(
        '--output-file', 
        default='optimization_rules_insertions.sql',
        help='Output SQL file (default: optimization_rules_insertions.sql)'
    )
    
    parser.add_argument(
        '--cluster-mode',
        action='store_true',
        help='Generate SQL for cluster mode (_local table)'
    )
    
    parser.add_argument(
        '--database',
        default='flamedb',
        help='ClickHouse database name (default: flamedb)'
    )
    
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose output'
    )
    
    args = parser.parse_args()
    
    # Convert YAML to SQL
    try:
        successful, failed = convert_yaml_to_sql(
            config_file=args.config_file,
            output_file=args.output_file,
            cluster_mode=args.cluster_mode,
            database=args.database,
            verbose=args.verbose
        )
        
        if failed > 0:
            sys.exit(1)  # Exit with error if any conversions failed
        
    except KeyboardInterrupt:
        print("\n⚠️  Conversion cancelled by user", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Conversion failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

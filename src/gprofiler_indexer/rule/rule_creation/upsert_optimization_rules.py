#!/usr/bin/env python3
"""
ClickHouse Optimization Rules Upsert Tool
==========================================
Simplified tool to insert optimization rules directly from YAML into ClickHouse.
Automatically fills default values and prevents duplica        sql_parts = [
            f"-- Optimization Rules Insert - Generated on {datetime.now().isoformat()}",
            f"-- Inserting {len(rules)} rules into {table_name}",
            "",
            f"INSERT INTO {table_name} (",
            "    rule_id, rule_name, callstack_pattern, platform_pattern,",
            "    technology_stack, rule_category, optimization_type,", 
            "    description, optimization_description,",
            "    relative_optimization_efficiency_min, relative_optimization_efficiency_max,",
            "    precision_score, accuracy_score,",
            "    implementation_complexity",
            ") VALUES"
        ]   python3 upsert_optimization_rules.py --config-file rules.yaml --clickhouse-host localhost
    python3 upsert_optimization_rules.py --config-file rules.yaml --clickhouse-host production-ch --dry-run
    python3 upsert_optimization_rules.py --config-file rules.yaml --cluster-mode --user admin --password secret

Features:
- Direct YAML to ClickHouse insertion
- Automatic default value filling (min/max efficiency, precision, accuracy)
- Duplicate prevention by rule_id
- Cluster mode support
- Dry-run mode to preview changes
- Comprehensive validation
- Human-readable YAML format with comments support

# Insert/update from YAML config file
python3 upsert_optimization_rules.py \
  --config-file optimization_rules.yaml \
  --clickhouse-host localhost \
  --user <your_user> --password <your_password> \
  --verbose

# Preview changes without executing
python3 upsert_optimization_rules.py \
  --config-file optimization_rules.yaml \
  --clickhouse-host clickhouse-host.com \
  --user default \
  --dry-run --verbose
"""

import argparse
import sys
import subprocess
import yaml
import tempfile
from typing import List, Set, Dict, Any, Optional
from pathlib import Path
import re
import time
from datetime import datetime

class OptimizationRulesUpsert:
    
    def __init__(self, host: str = "localhost", port: int = 9000, user: str = "default", 
                 password: str = "", database: str = "flamedb", cluster_mode: bool = False):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.cluster_mode = cluster_mode
        self.table_name = "optimization_rules" if cluster_mode else "optimization_rules"
        self.full_table_name = f"{database}.{self.table_name}"

    def execute_clickhouse_query(self, query: str, retries: int = 3) -> str:
        """Execute ClickHouse query with retry logic."""
        # If connecting to localhost, use Docker exec (ports not exposed in compose)
        if self.host in ['localhost', '127.0.0.1']:
            cmd = [
                "docker", "exec", "gprofiler-ps-clickhouse",
                "clickhouse-client",
                f"--user={self.user}",
                f"--database={self.database}",
                "--format=TSV"
            ]
            
            if self.password:
                cmd.append(f"--password={self.password}")
        else:
            # Standard connection for remote hosts
            cmd = [
                "clickhouse-client",
                f"--host={self.host}",
                f"--port={self.port}",
                f"--user={self.user}",
                f"--database={self.database}",
                "--format=TSV"
            ]
            
            if self.password:
                cmd.append(f"--password={self.password}")
        
        cmd.extend(["--query", query])
        
        for attempt in range(retries):
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    return result.stdout.strip()
                else:
                    print(f"ClickHouse error: {result.stderr.strip()}", file=sys.stderr)
                    if attempt == retries - 1:
                        raise RuntimeError(f"ClickHouse query failed: {result.stderr.strip()}")
            except subprocess.TimeoutExpired:
                print(f"Query timeout (attempt {attempt + 1}/{retries})", file=sys.stderr)
                if attempt == retries - 1:
                    raise RuntimeError("Query timeout after retries")
            except Exception as e:
                print(f"Query execution error (attempt {attempt + 1}/{retries}): {e}", file=sys.stderr)
                if attempt == retries - 1:
                    raise
            
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
        
        raise RuntimeError("Max retries exceeded")

    def check_connection(self) -> bool:
        """Test ClickHouse connection."""
        try:
            result = self.execute_clickhouse_query("SELECT 1")
            return result == "1"
        except Exception as e:
            print(f"Connection test failed: {e}", file=sys.stderr)
            return False

    def check_table_exists(self) -> bool:
        """Check if optimization rules table exists."""
        try:
            query = f"""
            SELECT count(*) 
            FROM system.tables 
            WHERE database = '{self.database}' AND name = '{self.table_name}'
            """
            result = self.execute_clickhouse_query(query)
            return int(result) > 0
        except Exception as e:
            print(f"Error checking table existence: {e}", file=sys.stderr)
            return False

    def get_existing_rule_ids(self) -> Set[str]:
        """Get set of existing rule IDs from database."""
        try:
            query = f"SELECT DISTINCT rule_id FROM {self.full_table_name}"
            result = self.execute_clickhouse_query(query)
            
            if not result:
                return set()
            
            rule_ids = set()
            # Split by actual newline character, not literal string
            for line in result.split('\n'):
                if line.strip():
                    rule_ids.add(line.strip())
            
            return rule_ids
        except Exception as e:
            print(f"Error getting existing rule IDs: {e}", file=sys.stderr)
            return set()

    def load_config_file(self, config_file: str) -> Dict[str, Any]:
        """Load rules from YAML config file."""
        config_path = Path(config_file)
        
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_file}")
        
        # Check if file has YAML extension
        if config_path.suffix.lower() not in ['.yaml', '.yml']:
            raise ValueError(f"Config file must be YAML (.yaml or .yml), got: {config_path.suffix}")
        
        try:
            with open(config_path, 'r', encoding='utf-8') as file:
                config = yaml.safe_load(file)
            
            if config is None:
                raise ValueError("Config file is empty or invalid YAML")
            
            return config
        except yaml.YAMLError as e:
            raise RuntimeError(f"YAML parsing error: {e}")
        except Exception as e:
            raise RuntimeError(f"Failed to load config file: {e}")

    def validate_rule(self, rule: Dict[str, Any]) -> List[str]:
        """Validate a single rule and return list of errors."""
        errors = []
        
        # Required fields
        required_fields = ['rule_id', 'rule_name', 'callstack_pattern', 'description', 'optimization_description']
        for field in required_fields:
            if not rule.get(field):
                errors.append(f"Missing required field: {field}")
        
        # Validate enums
        valid_categories = ['SOFTWARE', 'HARDWARE', 'UTILIZATION']
        if rule.get('category') and rule['category'] not in valid_categories:
            errors.append(f"Invalid category: {rule['category']}. Must be one of {valid_categories}")
     
        valid_complexities = ['EASY', 'MEDIUM', 'COMPLEX', 'VERY_COMPLEX']
        if rule.get('implementation_complexity') and rule['implementation_complexity'] not in valid_complexities:
            errors.append(f"Invalid implementation_complexity: {rule['implementation_complexity']}. Must be one of {valid_complexities}")
        
        valid_sources = ['COMMUNITY', 'PRIVATE', 'VERIFIED', 'EXPERIMENTAL']
        if rule.get('rule_source') and rule['rule_source'] not in valid_sources:
            errors.append(f"Invalid rule_source: {rule['rule_source']}. Must be one of {valid_sources}")
         
        return errors

    def fill_rule_defaults(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        """Fill default values for rule fields, including metrics and metadata as JSON strings."""
        import json
        defaults = {
            'technology_stack': rule.get('technology_stack', 'General'),
            'rule_category': rule.get('rule_category', 'GENERAL'),
            'platform_pattern': rule.get('platform_pattern', '.*'),
            'confidence_level': rule.get('confidence_level', 0.5),
            'impact_severity': rule.get('impact_severity', 'MEDIUM'),
            'implementation_complexity': rule.get('implementation_complexity', 'MEDIUM'),
            'rule_source': rule.get('rule_source', 'EXPERIMENTAL'),  # Default to EXPERIMENTAL
            'rule_status': 'EXPERIMENTAL',  # Always set to EXPERIMENTAL for new rules
            'tags': rule.get('tags', []),
            'documentation_url': rule.get('documentation_url', ''),
            'created_by': rule.get('created_by', 'upsert-script'),
            # Performance metrics - default to 0.0 as requested
            'relative_optimization_efficiency_min': 0.0,
            'relative_optimization_efficiency_max': 0.0,
            'precision_score': 0.5,  # Default precision as requested
            'accuracy_score': 0.5,   # Default accuracy as requested
            # New columns for metrics and metadata
            'metrics': json.dumps(rule.get('metrics', {}), ensure_ascii=False),
            'metadata': json.dumps(rule.get('metadata', {}), ensure_ascii=False),
        }
        # Merge defaults with rule
        filled_rule = {**defaults, **rule}
        # Ensure metrics and metadata are JSON strings
        for key in ['metrics', 'metadata']:
            if not isinstance(filled_rule[key], str):
                filled_rule[key] = json.dumps(filled_rule[key], ensure_ascii=False)
        return filled_rule

    def escape_sql_string(self, value: str) -> str:
        """Escape string for SQL insertion."""
        if value is None:
            return ""
        return value.replace("'", "\\'").replace("\\", "\\\\")

    def format_array_for_sql(self, array_values: List[str]) -> str:
        """Format array for ClickHouse SQL insertion."""
        if not array_values:
            return "[]"
        
        # Escape each string in the array
        escaped_values = [f"'{self.escape_sql_string(str(val))}'" for val in array_values]
        return f"[{', '.join(escaped_values)}]"

    def generate_insert_sql(self, rules: List[Dict[str, Any]]) -> str:
        """Generate ClickHouse INSERT SQL from rules, supporting metrics and metadata columns."""
        if not rules:
            return "-- No rules to insert"
        table_name = self.full_table_name
        sql_parts = [
            f"INSERT INTO {table_name} (",
            "    rule_id, rule_name, callstack_pattern, platform_pattern,",
            "    technology_stack, rule_category, optimization_type,",
            "    description, optimization_description,",
            "    relative_optimization_efficiency_min, relative_optimization_efficiency_max,",
            "    precision_score, accuracy_score,",
            "    implementation_complexity,",
            "    metrics, metadata",
            ") VALUES"
        ]
        values_list = []
        for rule in rules:
            values = f"""('{self.escape_sql_string(rule['rule_id'])}', '{self.escape_sql_string(rule['rule_name'])}', '{self.escape_sql_string(rule['callstack_pattern'])}', '{self.escape_sql_string(rule.get('platform_pattern', '.*'))}', '{self.escape_sql_string(rule['technology_stack'])}', '{self.escape_sql_string(rule['rule_category'])}', '{rule['optimization_type']}', '{self.escape_sql_string(rule['description'])}', '{self.escape_sql_string(rule['optimization_description'])}', {rule['relative_optimization_efficiency_min']}, {rule['relative_optimization_efficiency_max']}, {rule['precision_score']}, {rule['accuracy_score']}, '{rule['implementation_complexity']}', '{self.escape_sql_string(rule['metrics'])}', '{self.escape_sql_string(rule['metadata'])}')"""
            values_list.append(values)
        sql_parts.append(", ".join(values_list))
        sql_parts.append(";")
        return "\n".join(sql_parts)

    def upsert_from_config(self, config_file: str, dry_run: bool = False) -> Dict[str, Any]:
        """Load config file and upsert rules to ClickHouse."""
        
        # Load and validate config
        print(f"Loading config file: {config_file}")
        config = self.load_config_file(config_file)
        
        if 'rules' not in config:
            raise ValueError("Config file must contain 'rules' array")
        
        rules = config['rules']
        print(f"Found {len(rules)} rules in config")
        
        # Validate all rules
        print("Validating rules...")
        all_errors = []
        for i, rule in enumerate(rules):
            errors = self.validate_rule(rule)
            if errors:
                print(f"Rule {i+1} ({rule.get('rule_id', 'UNKNOWN')}): {', '.join(errors)}")
                all_errors.extend(errors)
        
        if all_errors:
            raise ValueError(f"Validation failed with {len(all_errors)} errors")
        
        # Fill defaults for all rules
        print("Filling default values...")
        filled_rules = []
        for rule in rules:
            filled_rule = self.fill_rule_defaults(rule)
            filled_rules.append(filled_rule)
        
        # Get existing rules and filter new ones
        print("Checking for existing rules...")
        existing_rule_ids = self.get_existing_rule_ids()
        print(f"Found {len(existing_rule_ids)} existing rules in database")
        
        new_rules = []
        skipped_rules = []
        
        for rule in filled_rules:
            rule_id = rule['rule_id']
            if rule_id not in existing_rule_ids:
                new_rules.append(rule)
            else:
                skipped_rules.append(rule_id)
        
        result = {
            'total_rules': len(rules),
            'existing_count': len(existing_rule_ids),
            'new_rules': [r['rule_id'] for r in new_rules],
            'skipped_rules': skipped_rules,
            'new_count': len(new_rules),
            'skipped_count': len(skipped_rules),
            'success': False
        }
        
        print(f"New rules to insert: {len(new_rules)}")
        print(f"Existing rules to skip: {len(skipped_rules)}")
        
        if not new_rules:
            print("✅ No new rules to insert - all rules already exist")
            result['success'] = True
            return result
        
        # Generate SQL
        insert_sql = self.generate_insert_sql(new_rules)
        
        if dry_run:
            print("\\n🔍 DRY RUN - SQL that would be executed:")
            print("=" * 80)
            print(insert_sql)
            print("=" * 80)
            print(f"\\n📋 New rules that would be inserted:")
            for rule_id in result['new_rules']:
                print(f"  • {rule_id}")
            result['success'] = True
            return result
        
        # Execute SQL
        try:
            print("Executing INSERT statement...")

            print(insert_sql)
            
            # Execute the INSERT SQL directly using our execute_clickhouse_query method
            result_output = self.execute_clickhouse_query(insert_sql)
            
            print(f"✅ Successfully inserted {len(new_rules)} new rules")
            result['success'] = True
                
        except Exception as e:
            print(f"❌ Error executing insert: {e}", file=sys.stderr)
            raise
        
        return result


def main():
    parser = argparse.ArgumentParser(description='Upsert optimization rules from YAML into ClickHouse')
    
    # Input
    parser.add_argument('--config-file', required=True, help='YAML config file with rules (.yaml or .yml)')
    
    # ClickHouse connection options
    parser.add_argument('--clickhouse-host', default='localhost', help='ClickHouse host (default: localhost)')
    parser.add_argument('--port', type=int, default=9000, help='ClickHouse port (default: 9000)')
    parser.add_argument('--user', default='default', help='ClickHouse user (default: default)')
    parser.add_argument('--password', default='', help='ClickHouse password')
    parser.add_argument('--database', default='flamedb', help='ClickHouse database (default: flamedb)')
    
    # Options
    parser.add_argument('--cluster-mode', action='store_true', help='Use cluster mode (_local table)')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without executing')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    try:
        # Initialize upserter
        upserter = OptimizationRulesUpsert(
            host=args.clickhouse_host,
            port=args.port,
            user=args.user,
            password=args.password,
            database=args.database,
            cluster_mode=args.cluster_mode
        )
        
        # Test connection
        print(f"Testing connection to ClickHouse at {args.clickhouse_host}:{args.port}...")
        if not upserter.check_connection():
            print("❌ Failed to connect to ClickHouse", file=sys.stderr)
            sys.exit(1)
        
        print("✅ Connected to ClickHouse")
        
        # Check if table exists
        if not upserter.check_table_exists():
            print(f"❌ Table {upserter.full_table_name} does not exist", file=sys.stderr)
            print("Please run the schema creation script first", file=sys.stderr)
            sys.exit(1)
        
        print(f"✅ Table {upserter.full_table_name} exists")
        
        # Execute upsert
        result = upserter.upsert_from_config(args.config_file, args.dry_run)
        
        # Print summary
        print("\\n📊 Summary:")
        print(f"  • Total rules in config: {result['total_rules']}")
        print(f"  • Existing rules in database: {result['existing_count']}")
        print(f"  • New rules inserted: {result['new_count']}")
        print(f"  • Existing rules skipped: {result['skipped_count']}")
        
        if args.verbose and result['new_rules']:
            print("\\n🆕 New rules added:")
            for rule_id in result['new_rules']:
                print(f"  • {rule_id}")
        
        if args.verbose and result['skipped_rules']:
            print("\\n⏭️ Existing rules skipped:")
            for rule_id in result['skipped_rules']:
                print(f"  • {rule_id}")
        
        if result['success']:
            print("\\n🎉 Operation completed successfully!")
            print("\\n💡 All new rules are set to EXPERIMENTAL status by default")
        else:
            print("\\n❌ Operation failed")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\\n⏹️ Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

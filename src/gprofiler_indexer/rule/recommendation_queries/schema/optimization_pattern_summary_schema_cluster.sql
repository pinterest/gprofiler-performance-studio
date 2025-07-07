-- OPTIMIZATION PATTERN SUMMARY TABLE SCHEMA (CLUSTER MODE)
-- Stores aggregated optimization pattern results for reporting and analytics

CREATE TABLE IF NOT EXISTS flamedb.optimization_pattern_summary_local ON CLUSTER '{cluster}' (
    -- CONTEXTUAL METADATA
    namespace String COMMENT 'Optional - Logical namespace or environment (e.g., prod, dev, team, business unit)',
    crdname String COMMENT 'Optional - Logical namespace or environment (e.g., prod, dev, team, business unit)',
    ServiceId String COMMENT 'Service identifier (can be empty for global patterns)',
    Technology LowCardinality(String) COMMENT 'Primary technology: Java, Python, Go, etc.',
    OptimizationPattern String COMMENT 'Regex pattern for optimization',
    ActionableRecommendation String COMMENT 'Optimization recommendation',
    ImplementationComplexity Enum8('EASY'=1, 'MEDIUM'=2, 'COMPLEX'=3, 'VERY_COMPLEX'=4) DEFAULT 'MEDIUM' COMMENT 'Implementation difficulty',
    RuleId String COMMENT 'Rule identifier',
    RuleName String COMMENT 'Rule name',
    RuleCategory LowCardinality(String) COMMENT 'Performance category',
    OptimizationType Enum8('HARDWARE'=1, 'SOFTWARE'=2, 'UTILIZATION'=3) COMMENT 'Type of optimization',
    RuleSource Enum8('COMMUNITY'=1, 'PRIVATE'=2, 'VERIFIED'=3, 'EXPERIMENTAL'=4) DEFAULT 'COMMUNITY' COMMENT 'Rule source',
    
    -- AGGREGATED METRICS
    TopAffectedStacks Array(String) DEFAULT [] COMMENT 'Top 5 affected call stacks',
    MinGlobalImpactPercent Float32 COMMENT 'Minimum estimated impact percent',
    MaxGlobalImpactPercent Float32 COMMENT 'Maximum estimated impact percent',
    PrecisionScore Float32 COMMENT 'Precision score',
    AccuracyScore Float32 COMMENT 'Accuracy score',
    AffectedStacks UInt32 COMMENT 'Number of affected stacks',
    TotalSamplesInPattern UInt64 COMMENT 'Total samples in pattern',
    RelativeResourceReductionPercentInService Float32 COMMENT 'Relative CPU percent in service',
    DollarImpact Float64 COMMENT 'Estimated dollar impact (optional, can be null)',
    NumHosts UInt32 COMMENT 'Number of unique hosts affected',
    
    -- AUDIT TRAIL
    created_date DateTime DEFAULT now() COMMENT 'When this summary was created',
    updated_date DateTime DEFAULT now() COMMENT 'When this summary was last modified',
    created_by String COMMENT 'Who created this summary (team, system, etc.)',
    
    -- HASH FOR DEDUPLICATION
    summary_hash UInt64 MATERIALIZED cityHash64(concat(RuleId,TopAffectedStacks,ServiceId, namespace,crdname)) COMMENT 'Hash for deduplication'
) ENGINE = ReplicatedMergeTree(
    '/clickhouse/{installation}/{cluster}/tables/{shard}/{database}/{table}',
    '{replica}'
)
PARTITION BY (Technology)
ORDER BY (Technology, RuleId)
SETTINGS index_granularity = 8192
COMMENT 'Aggregated optimization pattern summary for reporting and analytics';

-- Distributed table for cluster-wide access
CREATE TABLE IF NOT EXISTS flamedb.optimization_pattern_summary ON CLUSTER '{cluster}' AS flamedb.optimization_pattern_summary_local
ENGINE = Distributed(
    '{cluster}',
    'flamedb',
    'optimization_pattern_summary_local',
    summary_hash
)
COMMENT 'Distributed access to optimization pattern summary across the cluster';

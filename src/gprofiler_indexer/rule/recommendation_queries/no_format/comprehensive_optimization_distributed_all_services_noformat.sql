SET distributed_product_mode='allow';


WITH service_totals AS (
    SELECT 
        ServiceId,
        sum(NumSamples) as ServiceTotalSamples
    FROM flamedb.samples_1day 
    WHERE Timestamp >= now() - INTERVAL 2 DAY
    GROUP BY ServiceId
),
pattern_matches AS (
    SELECT DISTINCT
        s.ServiceId,
        s.CallStackName,
        r.rule_id,
        r.rule_name,
        r.technology_stack,
        r.rule_category,
        r.optimization_type,
        r.optimization_description,
        r.implementation_complexity,
        r.relative_optimization_efficiency_min,
        r.relative_optimization_efficiency_max,
        r.precision_score,
        r.accuracy_score,
        r.rule_source,
        r.tags,
        r.callstack_pattern
    FROM flamedb.samples_1day s
    GLOBAL CROSS JOIN flamedb.optimization_rules r
    WHERE s.Timestamp >= now() - INTERVAL 2 DAY
    AND s.CallStackName != ''
    AND match(s.CallStackName, r.callstack_pattern)
),
comprehensive_analysis AS (
    SELECT 
        s.ServiceId as ServiceId,
        s.CallStackName as CallStackName,
        pm.rule_id as RuleId,
        pm.rule_name as RuleName,
        pm.technology_stack as Technology,
        pm.rule_category as RuleCategory,
        pm.optimization_type as OptimizationType,
        pm.optimization_description as ActionableRecommendation,
        pm.implementation_complexity as ImplementationComplexity,
        pm.relative_optimization_efficiency_min as MinGlobalImpactPercent,
        pm.relative_optimization_efficiency_max as MaxGlobalImpactPercent,
        pm.precision_score as PrecisionScore,
        pm.accuracy_score as AccuracyScore,
        pm.rule_source as RuleSource,
        pm.tags as Tags,
        pm.callstack_pattern as OptimizationPattern,
        sum(s.NumSamples) as TotalPatternSamplesInService,
        round(sum(s.NumSamples) * 100.0 / st.ServiceTotalSamples, 4) as RelativePatternCpuPercentInService
    FROM flamedb.samples_1day s
    INNER JOIN  service_totals st ON s.ServiceId = st.ServiceId
    INNER JOIN  pattern_matches pm ON s.ServiceId = pm.ServiceId AND s.CallStackName = pm.CallStackName
    AND s.Timestamp >= now() - INTERVAL 2 DAY
    AND s.CallStackName != ''
    GROUP BY
        ServiceId,
        CallStackName,
        RuleId,
        RuleName,
        Technology,
        RuleCategory,
        OptimizationType,
        ActionableRecommendation,
        ImplementationComplexity,
        MinGlobalImpactPercent,
        MaxGlobalImpactPercent,
        PrecisionScore,
        AccuracyScore,
        RuleSource,
        Tags,
        OptimizationPattern,
        st.ServiceTotalSamples
    HAVING RelativePatternCpuPercentInService >= 0.001  -- Focus on stacks using >= 0.1% CPU
)
SELECT
    ServiceId,
    Technology,
    OptimizationPattern,
    ActionableRecommendation,
    ImplementationComplexity,
    RuleId,
    RuleName,
    RuleCategory,
    OptimizationType,
    RuleSource,
    arraySlice(groupArray(CallStackName), 1, 5) as TopAffectedStacks,
    min(MinGlobalImpactPercent) as MinGlobalImpactPercent,
    max(MaxGlobalImpactPercent) as MaxGlobalImpactPercent,
    max(PrecisionScore) as PrecisionScore,
    max(AccuracyScore) as AccuracyScore,
    count(*) as AffectedStacks,
    sum(TotalPatternSamplesInService) as TotalSamplesInPattern,
    round(sum(RelativePatternCpuPercentInService), 4) as RelativePatternResourcePercentInService
FROM comprehensive_analysis
GROUP BY
    ServiceId,
    Technology,
    OptimizationPattern,
    ActionableRecommendation,
    ImplementationComplexity,
    RuleId,
    RuleName,
    RuleCategory,
    OptimizationType,
    RuleSource
ORDER BY ServiceId, RelativePatternResourcePercentInService DESC
FORMAT CSVWithNames
-- Direct INSERT query without CTEs for ClickHouse compatibility
INSERT INTO flamedb.optimization_pattern_summary_local (
    namespace,
    crdname,
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
    TopAffectedStacks,
    MinGlobalImpactPercent,
    MaxGlobalImpactPercent,
    PrecisionScore,
    AccuracyScore,
    AffectedStacks,
    TotalSamplesInPattern,
    RelativeResourceReductionPercentInService,
    DollarImpact,
    NumHosts,
    created_date,
    updated_date,
    created_by
)
SELECT
    '' as namespace,
    '' as crdname,
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
    round(sum(RelativePatternCpuPercentInService), 4) as RelativeResourceReductionPercentInService,
    0.0 as DollarImpact,
    0 as NumHosts,
    now() as created_date,
    now() as updated_date,
    'automated_insert' as created_by
FROM (
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
        pm.callstack_pattern as OptimizationPattern,
        sum(s.NumSamples) as TotalPatternSamplesInService,
        round(sum(s.NumSamples) * 100.0 / st.ServiceTotalSamples, 4) as RelativePatternCpuPercentInService
    FROM flamedb.samples_1day_all_local s
    INNER JOIN (
        SELECT 
            ServiceId,
            sum(NumSamples) as ServiceTotalSamples
        FROM flamedb.samples_1day_all_local 
        WHERE Timestamp >= now() - INTERVAL 1 DAY
        GROUP BY ServiceId
    ) st ON s.ServiceId = st.ServiceId
    INNER JOIN (
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
        FROM flamedb.samples_1day_all_local s
        CROSS JOIN flamedb.optimization_rules r
        WHERE s.Timestamp >= now() - INTERVAL 1 DAY
        AND s.CallStackName != ''
        AND match(s.CallStackName, r.callstack_pattern)
    ) pm ON s.ServiceId = pm.ServiceId AND s.CallStackName = pm.CallStackName
    WHERE s.Timestamp >= now() - INTERVAL 1 DAY
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
        OptimizationPattern,
        st.ServiceTotalSamples
    HAVING RelativePatternCpuPercentInService >= 0.001
) comprehensive_analysis
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
ORDER BY ServiceId, RelativeResourceReductionPercentInService DESC

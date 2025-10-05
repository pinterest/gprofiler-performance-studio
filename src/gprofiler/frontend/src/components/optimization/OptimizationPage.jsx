{
    /*
     * Copyright (C) 2023 Intel Corporation
     *
     * Licensed under the Apache License, Version 2.0 (the "License");
     * you may not use this file except in compliance with the License.
     * You may obtain a copy of the License at
     *
     *    http://www.apache.org/licenses/LICENSE-2.0
     *
     * Unless required by applicable law or agreed to in writing, software
     * distributed under the License is distributed on an "AS IS" BASIS,
     * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
     * See the License for the specific language governing permissions and
     * limitations under the License.
     */
}

import React, { useState, useMemo } from 'react';
import { Typography, Chip, Box } from '@mui/material';
import PropTypes from 'prop-types';

import Flexbox from '../common/layout/Flexbox';
import useGetOptimizationRecommendations from '../../api/hooks/useGetOptimizationRecommendations';
import OptimizationFilters from './OptimizationFilters';
import OptimizationSummary from './OptimizationSummary';

const OptimizationPage = () => {
    const [filters, setFilters] = useState({
        serviceId: '',
        namespace: '',
        technology: '',
        complexity: '',
        optimizationType: '',
        ruleName: '',
        minImpact: 0,
        minPrecision: 0,
        minHosts: '',
    });

    const { recommendations = [], loading } = useGetOptimizationRecommendations(filters);

    const summaryData = useMemo(() => {
        if (!recommendations.length) return null;

        const totalRecommendations = recommendations.length;
        const uniqueServices = new Set(recommendations.map(r => r.ServiceId)).size;
        const uniqueTechnologies = new Set(recommendations.map(r => r.Technology)).size;
        const uniqueOptimizationTypes = new Set(recommendations.map(r => r.OptimizationType)).size;
        const totalStacks = recommendations.reduce((sum, r) => sum + r.AffectedStacks, 0);
        const totalHosts = recommendations.reduce((sum, r) => sum + (r.NumHosts || 0), 0);
        const avgImpact = recommendations.reduce((sum, r) => sum + r.RelativeResourceReductionPercentInService, 0) / totalRecommendations;
        const maxImpact = Math.max(...recommendations.map(r => r.RelativeResourceReductionPercentInService));

        const complexityBreakdown = recommendations.reduce((acc, r) => {
            acc[r.ImplementationComplexity] = (acc[r.ImplementationComplexity] || 0) + 1;
            return acc;
        }, {});

        return {
            totalRecommendations,
            uniqueServices,
            uniqueTechnologies,
            uniqueOptimizationTypes,
            totalStacks,
            totalHosts,
            avgImpact: avgImpact.toFixed(4),
            maxImpact: maxImpact.toFixed(4),
            easyFixes: complexityBreakdown.EASY || 0,
            mediumFixes: complexityBreakdown.MEDIUM || 0,
            complexFixes: complexityBreakdown.COMPLEX || 0,
            veryComplexFixes: complexityBreakdown.VERY_COMPLEX || 0
        };
    }, [recommendations]);

    const tableData = useMemo(() => {
        return recommendations.map((rec, index) => ({
            ...rec,
            id: `${rec.ServiceId}-${rec.RuleId}-${index}` // Create unique ID
        }));
    }, [recommendations]);

    const handleFilterChange = (newFilters) => {
        setFilters(prev => ({ ...prev, ...newFilters }));
    };

    return (
        <Flexbox column sx={{ p: 4, height: '100%' }} spacing={3}>
            <Flexbox justifyContent="space-between" alignItems="center">
                <Typography variant="h1" sx={{ color: 'text.primary' }}>
                    Performance Optimization Recommendations
                </Typography>
                {summaryData && (
                    <Chip
                        label={`${summaryData.totalRecommendations} recommendations found`}
                        color="primary"
                        variant="outlined"
                    />
                )}
            </Flexbox>

            {summaryData && (
                <OptimizationSummary data={summaryData} />
            )}

            <OptimizationFilters 
                filters={filters} 
                onFilterChange={handleFilterChange}
                loading={loading}
            />

            <Box sx={{ flexGrow: 1, minHeight: 0, mt: 3 }}>
                <Typography variant="h6" sx={{ mb: 2 }}>
                    Detailed Recommendations ({tableData.length})
                </Typography>
                
                {loading ? (
                    <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
                        <Typography>Loading recommendations...</Typography>
                    </Box>
                ) : tableData.length === 0 ? (
                    <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
                        <Typography>No recommendations found</Typography>
                    </Box>
                ) : (
                    <Box sx={{ overflowX: 'auto', border: '1px solid #e0e0e0', borderRadius: 1 }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                            <thead>
                                <tr style={{ backgroundColor: '#f5f5f5' }}>
                                    <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #e0e0e0' }}>Service</th>
                                    <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #e0e0e0' }}>Technology</th>
                                    <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #e0e0e0' }}>Recommendation</th>
                                    <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #e0e0e0' }}>Complexity</th>
                                    <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #e0e0e0' }}>Percent Impact</th>
                                    <th style={{ padding: '12px', textAlign: 'left', borderBottom: '1px solid #e0e0e0' }}>Affected Stacks</th>
                                </tr>
                            </thead>
                            <tbody>
                                {tableData.map((row, index) => (
                                    <tr key={row.id} style={{ backgroundColor: index % 2 === 0 ? '#fff' : '#f9f9f9' }}>
                                        <td style={{ padding: '12px', borderBottom: '1px solid #e0e0e0' }}>
                                            <strong>{row.ServiceId}</strong>
                                        </td>
                                        <td style={{ padding: '12px', borderBottom: '1px solid #e0e0e0' }}>
                                            <Chip label={row.Technology} size="small" />
                                        </td>
                                        <td style={{ padding: '12px', borderBottom: '1px solid #e0e0e0', maxWidth: '300px' }}>
                                            <Typography variant="body2" sx={{ wordBreak: 'break-word' }}>
                                                {row.ActionableRecommendation}
                                            </Typography>
                                        </td>
                                        <td style={{ padding: '12px', borderBottom: '1px solid #e0e0e0' }}>
                                            <Chip 
                                                label={row.ImplementationComplexity} 
                                                size="small" 
                                                color={
                                                    row.ImplementationComplexity === 'EASY' ? 'success' :
                                                    row.ImplementationComplexity === 'MEDIUM' ? 'warning' : 'error'
                                                }
                                            />
                                        </td>
                                        <td style={{ padding: '12px', borderBottom: '1px solid #e0e0e0' }}>
                                            <Typography variant="body2" sx={{ color: 'success.main', fontWeight: 'bold' }}>
                                                {row.RelativeResourceReductionPercentInService?.toFixed(4)}%
                                            </Typography>
                                        </td>
                                        <td style={{ padding: '12px', borderBottom: '1px solid #e0e0e0', maxWidth: '200px' }}>
                                            <Typography variant="caption" sx={{ wordBreak: 'break-word' }}>
                                                {row.TopAffectedStacks?.slice(0, 2).join(', ')}
                                                {row.AffectedStacks > 2 && ` (+${row.AffectedStacks - 2} more)`}
                                            </Typography>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </Box>
                )}
            </Box>
        </Flexbox>
    );
};

OptimizationPage.propTypes = {};

export default OptimizationPage;

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

import React from 'react';
import { 
    Card, 
    CardContent, 
    Typography, 
    Grid, 
    Chip,
    LinearProgress,
    Box
} from '@mui/material';
import PropTypes from 'prop-types';

import Flexbox from '../common/layout/Flexbox';

const SummaryCard = ({ icon, title, value, subtitle, color = 'primary' }) => (
    <Card sx={{ height: '100%' }}>
        <CardContent>
            <Flexbox alignItems="center" spacing={2} sx={{ mb: 1 }}>
                <span style={{ fontSize: '1.5rem' }}>{icon}</span>
                <Typography variant="h6" color="text.secondary">
                    {title}
                </Typography>
            </Flexbox>
            <Typography variant="h4" color={color} sx={{ fontWeight: 'bold', mb: 0.5 }}>
                {value}
            </Typography>
            {subtitle && (
                <Typography variant="body2" color="text.secondary">
                    {subtitle}
                </Typography>
            )}
        </CardContent>
    </Card>
);

const ComplexityBreakdown = ({ data }) => {
    const total = data.easyFixes + data.mediumFixes + data.complexFixes + data.veryComplexFixes;
    
    const complexityData = [
        { label: 'Easy', count: data.easyFixes, color: 'success' },
        { label: 'Medium', count: data.mediumFixes, color: 'warning' },
        { label: 'Complex', count: data.complexFixes, color: 'error' },
        { label: 'Very Complex', count: data.veryComplexFixes, color: 'error' },
    ];

    return (
        <Card sx={{ height: '100%' }}>
            <CardContent>
                <Flexbox alignItems="center" spacing={2} sx={{ mb: 2 }}>
                    <span style={{ fontSize: '1.5rem' }}>🔧</span>
                    <Typography variant="h6" color="text.secondary">
                        Implementation Complexity
                    </Typography>
                </Flexbox>
                
                <Flexbox column spacing={2}>
                    {complexityData.map((item) => (
                        <Box key={item.label}>
                            <Flexbox justifyContent="space-between" alignItems="center" sx={{ mb: 0.5 }}>
                                <Typography variant="body2">
                                    {item.label}
                                </Typography>
                                <Chip 
                                    label={item.count} 
                                    size="small" 
                                    color={item.color}
                                    variant="outlined"
                                />
                            </Flexbox>
                            <LinearProgress
                                variant="determinate"
                                value={total > 0 ? (item.count / total) * 100 : 0}
                                color={item.color}
                                sx={{ height: 6, borderRadius: 3 }}
                            />
                        </Box>
                    ))}
                </Flexbox>
            </CardContent>
        </Card>
    );
};

const OptimizationSummary = ({ data }) => {
    return (
        <Grid container spacing={3} sx={{ mb: 3 }}>
            {/* First Row - Main Metrics */}
            <Grid item xs={12} sm={6} md={2}>
                <SummaryCard
                    icon="📊"
                    title="Recommendations"
                    value={data.totalRecommendations}
                    subtitle="Total found"
                />
            </Grid>
            
            <Grid item xs={12} sm={6} md={2}>
                <SummaryCard
                    icon="💾"
                    title="Services"
                    value={data.uniqueServices}
                    subtitle="Affected services"
                    color="secondary"
                />
            </Grid>
            
            <Grid item xs={12} sm={6} md={2}>
                <SummaryCard
                    icon="💻"
                    title="Technologies"
                    value={data.uniqueTechnologies}
                    subtitle="Different stacks"
                    color="info"
                />
            </Grid>
            
            <Grid item xs={12} sm={6} md={2}>
                <SummaryCard
                    icon="⚡"
                    title="Call Stacks"
                    value={data.totalStacks}
                    subtitle="Total affected"
                    color="warning"
                />
            </Grid>
            
            <Grid item xs={12} sm={6} md={2}>
                <SummaryCard
                    icon="📈"
                    title="Avg Impact"
                    value={`${data.avgImpact}%`}
                    subtitle="Percent reduction"
                    color="success"
                />
            </Grid>
            
            <Grid item xs={12} sm={6} md={2}>
                <SummaryCard
                    icon="🚀"
                    title="Max Impact"
                    value={`${data.maxImpact}%`}
                    subtitle="Highest potential"
                    color="success"
                />
            </Grid>

            {/* Second Row - Additional Metrics */}
            <Grid item xs={12} sm={6} md={2}>
                <SummaryCard
                    icon="🏠"
                    title="Total Hosts"
                    value={data.totalHosts}
                    subtitle="Affected hosts"
                    color="info"
                />
            </Grid>

            <Grid item xs={12} sm={6} md={2}>
                <SummaryCard
                    icon="⚙️"
                    title="Optimization Types"
                    value={data.uniqueOptimizationTypes}
                    subtitle="Different types"
                    color="secondary"
                />
            </Grid>
            
            <Grid item xs={12} md={8}>
                <ComplexityBreakdown data={data} />
            </Grid>
        </Grid>
    );
};

OptimizationSummary.propTypes = {
    data: PropTypes.shape({
        totalRecommendations: PropTypes.number.isRequired,
        uniqueServices: PropTypes.number.isRequired,
        uniqueTechnologies: PropTypes.number.isRequired,
        totalStacks: PropTypes.number.isRequired,
        avgImpact: PropTypes.string.isRequired,
        maxImpact: PropTypes.string.isRequired,
        totalHosts: PropTypes.number.isRequired,
        uniqueOptimizationTypes: PropTypes.number.isRequired,
        easyFixes: PropTypes.number.isRequired,
        mediumFixes: PropTypes.number.isRequired,
        complexFixes: PropTypes.number.isRequired,
        veryComplexFixes: PropTypes.number.isRequired,
    }).isRequired,
};

SummaryCard.propTypes = {
    icon: PropTypes.element.isRequired,
    title: PropTypes.string.isRequired,
    value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]).isRequired,
    subtitle: PropTypes.string,
    color: PropTypes.string,
};

ComplexityBreakdown.propTypes = {
    data: PropTypes.object.isRequired,
};

export default OptimizationSummary;

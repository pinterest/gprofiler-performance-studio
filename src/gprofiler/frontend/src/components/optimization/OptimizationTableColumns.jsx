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
import { Typography, Chip, Box, Tooltip } from '@mui/material';

import Flexbox from '../common/layout/Flexbox';

const getComplexityColor = (complexity) => {
    switch (complexity) {
        case 'EASY': return 'success';
        case 'MEDIUM': return 'warning';
        case 'COMPLEX': return 'error';
        case 'VERY_COMPLEX': return 'error';
        default: return 'default';
    }
};

const getOptimizationTypeIcon = (type) => {
    switch (type) {
        case 'SOFTWARE': return '💻';
        case 'HARDWARE': return '🔧';
        case 'UTILIZATION': return '⚙️';
        default: return '📈';
    }
};

const ServiceCell = ({ cell }) => (
    <Tooltip title={cell.value} placement="top">
        <Typography variant="body2" sx={{ fontWeight: 'medium', color: 'primary.main' }}>
            {cell.value}
        </Typography>
    </Tooltip>
);

const TechnologyCell = ({ cell }) => (
    <Chip 
        label={cell.value} 
        size="small" 
        variant="outlined"
        color="primary"
    />
);

const RecommendationCell = ({ cell }) => (
    <Box sx={{ maxWidth: 400 }}>
        <Typography variant="body2" sx={{ 
            overflow: 'hidden', 
            textOverflow: 'ellipsis',
            display: '-webkit-box',
            WebkitLineClamp: 3,
            WebkitBoxOrient: 'vertical',
        }}>
            {cell.value}
        </Typography>
    </Box>
);

const ComplexityCell = ({ cell }) => (
    <Chip 
        label={cell.value.replace('_', ' ')} 
        size="small" 
        color={getComplexityColor(cell.value)}
        variant="filled"
    />
);

const OptimizationTypeCell = ({ cell }) => (
    <Flexbox alignItems="center" spacing={1}>
        <span>{getOptimizationTypeIcon(cell.value)}</span>
        <Typography variant="body2">
            {cell.value}
        </Typography>
    </Flexbox>
);

const ImpactCell = ({ cell }) => (
    <Flexbox alignItems="center" spacing={1}>
        <span>📈</span>
        <Typography variant="body2" sx={{ fontWeight: 'medium', color: 'success.main' }}>
            {cell.value.toFixed(4)}%
        </Typography>
    </Flexbox>
);

const StacksCell = ({ cell }) => {
    const stacks = cell.row.TopAffectedStacks || [];
    const affectedCount = cell.row.AffectedStacks || 0;
    
    return (
        <Box>
            <Typography variant="body2" sx={{ fontWeight: 'medium', mb: 0.5 }}>
                {affectedCount} affected stacks
            </Typography>
            {stacks.slice(0, 2).map((stack, index) => (
                <Tooltip key={index} title={stack} placement="top">
                    <Typography 
                        variant="caption" 
                        sx={{ 
                            display: 'block',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            maxWidth: 200,
                            color: 'text.secondary'
                        }}
                    >
                        {stack}
                    </Typography>
                </Tooltip>
            ))}
            {stacks.length > 2 && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                    +{stacks.length - 2} more...
                </Typography>
            )}
        </Box>
    );
};

export const OPTIMIZATION_TABLE_COLUMNS = [
    {
        headerName: 'Service',
        field: 'ServiceId',
        renderCell: ServiceCell,
        width: 120,
        sortable: true,
    },
    {
        headerName: 'Technology',
        field: 'Technology',
        renderCell: TechnologyCell,
        width: 100,
        sortable: true,
    },
    {
        headerName: 'Recommendation',
        field: 'ActionableRecommendation',
        renderCell: RecommendationCell,
        flex: 1,
        minWidth: 300,
        sortable: false,
    },
    {
        headerName: 'Complexity',
        field: 'ImplementationComplexity',
        renderCell: ComplexityCell,
        width: 120,
        sortable: true,
    },
    {
        headerName: 'Type',
        field: 'OptimizationType',
        renderCell: OptimizationTypeCell,
        width: 130,
        sortable: true,
    },
    {
        headerName: 'CPU Impact',
        field: 'RelativeResourceReductionPercentInService',
        renderCell: ImpactCell,
        width: 120,
        sortable: true,
        type: 'number',
    },
    {
        headerName: 'Affected Stacks',
        field: 'AffectedStacks',
        renderCell: StacksCell,
        width: 200,
        sortable: true,
        type: 'number',
    },
    {
        headerName: 'Rule',
        field: 'RuleName',
        width: 150,
        sortable: true,
        renderCell: ({ cell }) => (
            <Tooltip title={cell.value} placement="top">
                <Typography variant="body2" sx={{ 
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap'
                }}>
                    {cell.value}
                </Typography>
            </Tooltip>
        ),
    },
];

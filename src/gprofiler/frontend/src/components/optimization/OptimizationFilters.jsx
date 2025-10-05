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
    TextField, 
    MenuItem, 
    Slider, 
    Typography, 
    Card, 
    CardContent,
    Grid,
    FormControl,
    InputLabel,
    Select
} from '@mui/material';
import PropTypes from 'prop-types';

import Flexbox from '../common/layout/Flexbox';

const COMPLEXITY_OPTIONS = [
    { value: '', label: 'All Complexities' },
    { value: 'EASY', label: 'Easy' },
    { value: 'MEDIUM', label: 'Medium' },
    { value: 'COMPLEX', label: 'Complex' },
    { value: 'VERY_COMPLEX', label: 'Very Complex' },
];

const TECHNOLOGY_OPTIONS = [
    { value: '', label: 'All Technologies' },
    { value: 'Java', label: 'Java' },
    { value: 'Python', label: 'Python' },
    { value: 'Go', label: 'Go' },
    { value: 'JavaScript', label: 'JavaScript' },
    { value: 'C++', label: 'C++' },
];

const OPTIMIZATION_TYPE_OPTIONS = [
    { value: '', label: 'All Types' },
    { value: 'SOFTWARE', label: 'Software' },
    { value: 'UTILIZATION', label: 'Utilization' },
];

const DAYS_BACK_OPTIONS = [
    { value: 1, label: 'Last 24 hours' },
    { value: 3, label: 'Last 3 days' },
    { value: 7, label: 'Last week' },
    { value: 30, label: 'Last month' },
];

const OptimizationFilters = ({ filters, onFilterChange, loading }) => {
    const handleChange = (field) => (event) => {
        onFilterChange({ [field]: event.target.value });
    };

    const handleSliderChange = (field) => (_, newValue) => {
        onFilterChange({ [field]: newValue });
    };

    return (
        <Card sx={{ mb: 3 }}>
            <CardContent>
                <Typography variant="h6" sx={{ mb: 2 }}>
                    Filter Recommendations
                </Typography>
                
                <Grid container spacing={3}>
                    {/* First Row - Primary Filters */}
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            fullWidth
                            label="Service ID"
                            value={filters.serviceId}
                            onChange={handleChange('serviceId')}
                            disabled={loading}
                            placeholder="e.g., 10, 20, 30"
                            size="small"
                        />
                    </Grid>
                    
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            fullWidth
                            label="Namespace"
                            value={filters.namespace}
                            onChange={handleChange('namespace')}
                            disabled={loading}
                            placeholder="e.g., default, prod"
                            size="small"
                        />
                    </Grid>
                    
                    <Grid item xs={12} sm={6} md={3}>
                        <FormControl fullWidth size="small">
                            <InputLabel>Technology</InputLabel>
                            <Select
                                value={filters.technology}
                                onChange={handleChange('technology')}
                                disabled={loading}
                                label="Technology"
                            >
                                {TECHNOLOGY_OPTIONS.map((option) => (
                                    <MenuItem key={option.value} value={option.value}>
                                        {option.label}
                                    </MenuItem>
                                ))}
                            </Select>
                        </FormControl>
                    </Grid>
                    
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            fullWidth
                            label="Minimum Hosts"
                            value={filters.minHosts}
                            onChange={handleChange('minHosts')}
                            disabled={loading}
                            placeholder="e.g., 1, 5, 10"
                            type="number"
                            size="small"
                        />
                    </Grid>

                    {/* Second Row - Secondary Filters */}
                    <Grid item xs={12} sm={6} md={3}>
                        <FormControl fullWidth size="small">
                            <InputLabel>Complexity</InputLabel>
                            <Select
                                value={filters.complexity}
                                onChange={handleChange('complexity')}
                                disabled={loading}
                                label="Complexity"
                            >
                                {COMPLEXITY_OPTIONS.map((option) => (
                                    <MenuItem key={option.value} value={option.value}>
                                        {option.label}
                                    </MenuItem>
                                ))}
                            </Select>
                        </FormControl>
                    </Grid>
                    
                    <Grid item xs={12} sm={6} md={3}>
                        <FormControl fullWidth size="small">
                            <InputLabel>Optimization Type</InputLabel>
                            <Select
                                value={filters.optimizationType}
                                onChange={handleChange('optimizationType')}
                                disabled={loading}
                                label="Optimization Type"
                            >
                                {OPTIMIZATION_TYPE_OPTIONS.map((option) => (
                                    <MenuItem key={option.value} value={option.value}>
                                        {option.label}
                                    </MenuItem>
                                ))}
                            </Select>
                        </FormControl>
                    </Grid>

                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            fullWidth
                            label="Rule Name"
                            value={filters.ruleName}
                            onChange={handleChange('ruleName')}
                            disabled={loading}
                            placeholder="e.g., Finagle Filter Chain"
                            size="small"
                        />
                    </Grid>
                    
                    <Grid item xs={12} md={6}>
                        <Flexbox column spacing={1} sx={{ pr: 6 }}>
                            <Typography variant="body2" color="text.secondary">
                                Minimum Percent Impact: {filters.minImpact}%
                            </Typography>
                            <Slider
                                value={filters.minImpact}
                                onChange={handleSliderChange('minImpact')}
                                disabled={loading}
                                min={0}
                                max={100}
                                step={1}
                                marks={[
                                    { value: 0, label: '0%' },
                                    { value: 25, label: '25%' },
                                    { value: 50, label: '50%' },
                                    { value: 75, label: '75%' },
                                    { value: 100, label: '100%' },
                                ]}
                                valueLabelDisplay="auto"
                                valueLabelFormat={(value) => `${value}%`}
                            />
                        </Flexbox>
                    </Grid>
                    
                    <Grid item xs={12} md={6}>
                        <Flexbox column spacing={1} sx={{ pl: 6 }}>
                            <Typography variant="body2" color="text.secondary">
                                Minimum Precision: {filters.minPrecision}%
                            </Typography>
                            <Slider
                                value={filters.minPrecision}
                                onChange={handleSliderChange('minPrecision')}
                                disabled={loading}
                                min={0}
                                max={100}
                                step={1}
                                marks={[
                                    { value: 0, label: '0%' },
                                    { value: 25, label: '25%' },
                                    { value: 50, label: '50%' },
                                    { value: 75, label: '75%' },
                                    { value: 100, label: '100%' },
                                ]}
                                valueLabelDisplay="auto"
                                valueLabelFormat={(value) => `${value}%`}
                            />
                        </Flexbox>
                    </Grid>
                </Grid>
            </CardContent>
        </Card>
    );
};

OptimizationFilters.propTypes = {
    filters: PropTypes.object.isRequired,
    onFilterChange: PropTypes.func.isRequired,
    loading: PropTypes.bool,
};

export default OptimizationFilters;

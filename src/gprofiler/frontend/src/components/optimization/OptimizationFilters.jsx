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
                    
                    <Grid item xs={12} md={6}>
                        <Flexbox column spacing={1}>
                            <Typography variant="body2" color="text.secondary">
                                Minimum CPU Impact: {filters.minImpact}%
                            </Typography>
                            <Slider
                                value={filters.minImpact}
                                onChange={handleSliderChange('minImpact')}
                                disabled={loading}
                                min={0}
                                max={10}
                                step={0.1}
                                marks={[
                                    { value: 0, label: '0%' },
                                    { value: 1, label: '1%' },
                                    { value: 5, label: '5%' },
                                    { value: 10, label: '10%' },
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

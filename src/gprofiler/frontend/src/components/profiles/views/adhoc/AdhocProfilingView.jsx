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

import { useContext, useEffect, useState } from 'react';
import { Box, Typography, Select, MenuItem, FormControl, InputLabel, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper, Button } from '@mui/material';
import { SelectorsContext } from '@/states';
import { FilterTagsContext } from '@/states/filters/FiltersTagsContext';
import useFetchWithRequest from '@/api/useFetchWithRequest';
import { DATA_URLS } from '@/api/urls';
import { stringify } from 'query-string';
import { getStartEndDateTimeFromSelection } from '@/api/utils';
import { format } from 'date-fns';
import Flexbox from '@/components/common/layout/Flexbox';

const AdhocProfilingView = () => {
    const { selectedService, timeSelection, selectedHost } = useContext(SelectorsContext);
    const { activeFilterTag } = useContext(FilterTagsContext);
    const [selectedFile, setSelectedFile] = useState(null);
    const [selectedFileContent, setSelectedFileContent] = useState(null);

    const timeParams = getStartEndDateTimeFromSelection(timeSelection);
    const hostFilter = selectedHost ? `HostName = "${selectedHost}"` : '';
    const filter = activeFilterTag?.filter ? JSON.stringify({ ...activeFilterTag, filter: `${activeFilterTag.filter} AND ${hostFilter}` }) : JSON.stringify({ filter: hostFilter });

    const { data: filesData, loading: filesLoading, error: filesError, run: fetchFiles } = useFetchWithRequest(
        {
            url: `${DATA_URLS.GET_ADHOC_FLAMEGRAPHS}?${stringify({
                serviceName: selectedService,
                ...timeParams,
                filter: filter,
            })}`,
        },
        { manual: true }
    );

    const { data: fileContent, loading: contentLoading, error: contentError, run: fetchFileContent } = useFetchWithRequest(
        {
            url: `${DATA_URLS.GET_ADHOC_FLAMEGRAPH_CONTENT}?${stringify({
                serviceName: selectedService,
                filename: selectedFile?.filename,
            })}`,
        },
        { manual: true }
    );

    useEffect(() => {
        if (selectedService && timeSelection) {
            fetchFiles();
            setSelectedFile(null);
            setSelectedFileContent(null);
        }
    }, [selectedService, timeSelection, activeFilterTag, selectedHost, fetchFiles]);

    useEffect(() => {
        if (selectedFile) {
            fetchFileContent();
        }
    }, [selectedFile, fetchFileContent]);

    useEffect(() => {
        if (fileContent) {
            setSelectedFileContent(fileContent.content);
        } else {
            setSelectedFileContent(null);
        }
    }, [fileContent]);

    const handleFileSelect = (event) => {
        const filename = event.target.value;
        const file = filesData?.files.find(f => f.filename === filename);
        setSelectedFile(file);
    };

    const handleRowClick = (file) => {
        setSelectedFile(file);
    };

    if (filesLoading) {
        return <Typography>Loading adhoc flamegraphs...</Typography>;
    }

    if (filesError) {
        return <Typography color="error">Error loading adhoc flamegraphs: {filesError.message}</Typography>;
    }

    return (
        <Flexbox column sx={{ height: '100%', width: '100%', p: 2 }}>
            <Box sx={{ mb: 2, display: 'flex', alignItems: 'center', gap: 2 }}>
                <Typography variant="h6">Adhoc Profiling</Typography>
                {filesData?.files && filesData.files.length > 0 && (
                    <FormControl sx={{ minWidth: 200 }}>
                        <InputLabel id="select-file-label">Select Flamegraph File</InputLabel>
                        <Select
                            labelId="select-file-label"
                            value={selectedFile?.filename || ''}
                            label="Select Flamegraph File"
                            onChange={handleFileSelect}
                        >
                            {filesData.files.map((file) => (
                                <MenuItem key={file.filename} value={file.filename}>
                                    {format(new Date(file.timestamp), 'yyyy-MM-dd HH:mm:ss')} - {file.hostname || 'N/A'}
                                </MenuItem>
                            ))}
                        </Select>
                    </FormControl>
                )}
            </Box>

            {filesData?.files && filesData.files.length > 0 ? (
                <Box sx={{ flexGrow: 1, display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <TableContainer component={Paper} sx={{ maxHeight: 300, overflowY: 'auto' }}>
                        <Table stickyHeader size="small">
                            <TableHead>
                                <TableRow>
                                    <TableCell>Timestamp</TableCell>
                                    <TableCell>Hostname</TableCell>
                                    <TableCell>Size</TableCell>
                                    <TableCell>Action</TableCell>
                                </TableRow>
                            </TableHead>
                            <TableBody>
                                {filesData.files.map((file) => (
                                    <TableRow
                                        key={file.filename}
                                        onClick={() => handleRowClick(file)}
                                        selected={selectedFile?.filename === file.filename}
                                        sx={{ cursor: 'pointer', '&:hover': { backgroundColor: 'action.hover' } }}
                                    >
                                        <TableCell>{format(new Date(file.timestamp), 'yyyy-MM-dd HH:mm:ss')}</TableCell>
                                        <TableCell>{file.hostname || 'N/A'}</TableCell>
                                        <TableCell>{(file.size / 1024).toFixed(2)} KB</TableCell>
                                        <TableCell>
                                            <Button size="small" onClick={() => handleRowClick(file)}>View</Button>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    </TableContainer>

                    {contentLoading && <Typography>Loading flamegraph content...</Typography>}
                    {contentError && <Typography color="error">Error loading flamegraph content: {contentError.message}</Typography>}
                    {selectedFileContent && (
                        <Box sx={{ flexGrow: 1, position: 'relative', width: '100%' }}>
                            <iframe
                                style={{
                                    position: 'absolute',
                                    margin: 0,
                                    width: '100%',
                                    height: '100%',
                                    border: 'none',
                                }}
                                title="Adhoc Flamegraph"
                                srcDoc={selectedFileContent}
                            />
                        </Box>
                    )}
                </Box>
            ) : (
                <Typography>No adhoc flamegraphs found for the selected service and time range.</Typography>
            )}
        </Flexbox>
    );
};

export default AdhocProfilingView;
//
// Copyright (C) 2023 Intel Corporation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//

package main

import (
	"context"
	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
	"gopkg.in/natefinch/lumberjack.v2"
	"os"
	"os/signal"
	"sync"
	"syscall"
)

var (
	frameReplacer *FrameReplacer
	logger        *zap.SugaredLogger
)

type RecordChannels struct {
	StacksRecords  chan StackRecord
	MetricsRecords chan MetricRecord
}

func InitLogs(args *CLIArgs) {
	var zapLogger *zap.Logger
	var err error
	
	if args.LogFilePath != "" {
		// Configure zap with log rotation using lumberjack
		logRotator := &lumberjack.Logger{
			Filename:   args.LogFilePath,
			MaxSize:    args.LogMaxSize,    // MB
			MaxBackups: args.LogMaxBackups, // number of backup files
			MaxAge:     args.LogMaxAge,     // days
			Compress:   args.LogCompress,   // compress rotated files
		}
		
		// Create a write syncer for file output with rotation
		fileWriteSyncer := zapcore.AddSync(logRotator)
		
		// Create write syncers for console output
		consoleWriteSyncer := zapcore.AddSync(os.Stdout)
		consoleErrorWriteSyncer := zapcore.AddSync(os.Stderr)
		
		// Create encoder config
		encoderConfig := zap.NewDevelopmentEncoderConfig()
		
		// Console encoder (human readable)
		consoleEncoder := zapcore.NewConsoleEncoder(encoderConfig)
		
		// File encoder (JSON format for better parsing)
		fileEncoderConfig := zap.NewProductionEncoderConfig()
		fileEncoderConfig.TimeKey = "timestamp"
		fileEncoderConfig.EncodeTime = zapcore.ISO8601TimeEncoder
		fileEncoder := zapcore.NewJSONEncoder(fileEncoderConfig)
		
		// Create cores for different outputs
		consoleCore := zapcore.NewCore(consoleEncoder, consoleWriteSyncer, zapcore.DebugLevel)
		consoleErrorCore := zapcore.NewCore(consoleEncoder, consoleErrorWriteSyncer, zapcore.ErrorLevel)
		fileCore := zapcore.NewCore(fileEncoder, fileWriteSyncer, zapcore.DebugLevel)
		
		// Combine cores
		core := zapcore.NewTee(consoleCore, consoleErrorCore, fileCore)
		
		// Create logger
		zapLogger = zap.New(core, zap.AddCaller(), zap.AddStacktrace(zapcore.ErrorLevel))
		
		if err != nil {
			// Fallback to development logger if file logging fails
			zapLogger, _ = zap.NewDevelopment()
			zapLogger.Error("Failed to initialize file logging, falling back to console only", zap.Error(err))
		}
	} else {
		// Use default development logger (console only)
		zapLogger, _ = zap.NewDevelopment()
	}
	
	logger = zapLogger.Sugar()
}

func main() {
	// Initialize with basic logging first (no file logging yet)
	basicArgs := &CLIArgs{}
	InitLogs(basicArgs)
	
	args := NewCliArgs()
	args.ParseArgs()
	
	// Re-initialize logging with full configuration including rotation
	InitLogs(args)

	logger.Infof("Starting %s", AppName)
	tasks := make(chan SQSMessage, args.Concurrency)
	channels := RecordChannels{
		StacksRecords:  make(chan StackRecord, args.ClickHouseStacksBatchSize),
		MetricsRecords: make(chan MetricRecord, args.ClickHouseMetricsBatchSize),
	}
	ctx, cancelFunc := context.WithCancel(context.Background())
	var tasksWaitGroup sync.WaitGroup
	var listenSQSWaitGroup sync.WaitGroup
	var buffWriterWaitGroup sync.WaitGroup

	frameReplacer = NewFrameReplacer()
	frameReplacer.InitRegexps(args.FrameReplaceFileName)
	callStackWriter := NewProfilesWriter(&channels)

	reloader, watcherErr := NewFileReloader(args)
	reloader.Start(ctx)
	if watcherErr == nil {
		files := []string{args.FrameReplaceFileName}
		for _, filename := range files {
			err := reloader.Add(filename)
			if err != nil {
				logger.Fatalf("unable add reloader to file %s, %v", filename, err)
			}
			reloader.LoadFile(filename)
		}
	} else {
		logger.Warnf("Unable to create reloader %v", watcherErr)
	}
	// spawn workers
	for idx := 0; idx < args.Concurrency; idx++ {
		tasksWaitGroup.Add(1)
		go Worker(idx, args, tasks, callStackWriter, &tasksWaitGroup)
	}

	if args.InputFolder == "" {
		logger.Debugf("start listening SQS queue %s", args.SQSQueue)
		listenSQSWaitGroup.Add(1)
		go ListenSqs(ctx, args, tasks, &listenSQSWaitGroup)
	} else {
		listenSQSWaitGroup.Add(1)
		go ProcessFolder(ctx, tasks, args.InputFolder, &listenSQSWaitGroup)
	}

	buffWriterWaitGroup.Add(1)
	go BufferedClickHouseWrite(args, &channels, &buffWriterWaitGroup)

	signalChannel := make(chan os.Signal, 2)
	signal.Notify(signalChannel, os.Interrupt, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		for sig := range signalChannel {
			logger.Debugf("signal %s received", sig)
			cancelFunc()
			listenSQSWaitGroup.Wait()
			close(tasks)
			tasksWaitGroup.Wait()
			close(channels.StacksRecords)
			close(channels.MetricsRecords)
		}
	}()

	buffWriterWaitGroup.Wait()
	logger.Info("Graceful shutdown")
}

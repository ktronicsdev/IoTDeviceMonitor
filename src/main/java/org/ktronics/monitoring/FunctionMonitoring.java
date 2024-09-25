package org.ktronics.monitoring;

import io.micrometer.core.instrument.Counter;
import io.micrometer.prometheus.PrometheusMeterRegistry;

public class FunctionMonitoring {
    private final PrometheusMeterRegistry registry;
    private final Counter successCounter;
    private final Counter failureCounter;

    public FunctionMonitoring(PrometheusMeterRegistry registry) {
        this.registry = registry;
        this.successCounter = registry.counter("function_execution_success");
        this.failureCounter = registry.counter("function_execution_failure");
    }

    public void onSuccess() {
        successCounter.increment();
    }

    public void onFailure() {
        failureCounter.increment();
    }

    public String getMetrics() {
        return registry.scrape();
    }
}


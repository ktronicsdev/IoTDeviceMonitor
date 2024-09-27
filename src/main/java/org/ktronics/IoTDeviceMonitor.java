package org.ktronics;

import com.microsoft.azure.functions.annotation.*;
import com.microsoft.azure.functions.*;
import io.micrometer.prometheus.PrometheusConfig;
import io.micrometer.prometheus.PrometheusMeterRegistry;
import org.ktronics.models.Credential;
import org.ktronics.monitoring.FunctionMonitoring;
import org.ktronics.services.DatabaseService;
import org.ktronics.services.MongoDatabaseService;
import org.ktronics.services.PowerCheckService;

import java.util.List;
import java.util.Optional;

public class IoTDeviceMonitor {

    private final FunctionMonitoring monitoring;

    public IoTDeviceMonitor() {
        this.monitoring = new FunctionMonitoring(new PrometheusMeterRegistry(PrometheusConfig.DEFAULT));
    }

    @FunctionName("powerPlantMonitor")
    public void run(
            @TimerTrigger(name = "powerPlantMonitorTimerTrigger", schedule = "%TIMER_SCHEDULE%") String timerInfo,
            final ExecutionContext context
    ) {
        context.getLogger().info("Azure Function triggered: " + timerInfo);

        DatabaseService databaseService = new MongoDatabaseService();
        PowerCheckService powerCheckService = new PowerCheckService();

        List<Credential> credentials = databaseService.getCredentials();

        try {
            credentials.forEach(credential -> {
                if ("ShineMonitor".equals(credential.getType())) {
                    var shineMonitorStatus = powerCheckService.checkPowerStationsForUser(credential);
                    for (String status : shineMonitorStatus) {
                        context.getLogger().info(status);
                    }
                }
            });
            monitoring.onSuccess();
        }
        catch (Exception e) {
            monitoring.onFailure();
            context.getLogger().severe("Error occurred: " + e.getMessage());
        }
    }

    @FunctionName("getMetrics")
    public HttpResponseMessage getMetrics(
            @HttpTrigger(name = "getMetrics", methods = {HttpMethod.GET}, route = "metrics") HttpRequestMessage<Optional<String>> request,
            final ExecutionContext context
    ) {
        String metrics = monitoring.getMetrics();
        return request.createResponseBuilder(HttpStatus.OK).body(metrics).build();
    }
}
package org.ktronics;

import com.microsoft.azure.functions.annotation.*;
import com.microsoft.azure.functions.*;
import io.micrometer.prometheus.PrometheusConfig;
import io.micrometer.prometheus.PrometheusMeterRegistry;
import org.ktronics.models.Credential;
import org.ktronics.monitoring.FunctionMonitoring;
import org.ktronics.services.DatabaseService;
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
        PowerCheckService powerCheckService = new PowerCheckService();
        List<Credential> credentials = new DatabaseService().getCredentials();

        try {
            credentials.forEach(credential -> {
                if ("ShineMonitor".equals(credential.getType())) {
                    // TODO : move the shine monitor check to abnormal detector instead of IoT monitor
                    var shineMonitorStatus = powerCheckService.checkPowerStationsForUser(credential);
                    for (var status : shineMonitorStatus) {
                        context.getLogger().debug(status);
                    }
                }
            });
            monitoring.onSuccess();
        }
        catch (Exception e) {
            monitoring.onFailure();
             // TODO : Please log faliur occured time since u logged triggered time 
            context.getLogger().severe("Error occurred: " + e.getMessage(), time occured ??);
        }

        // TODO : Please log completed time since u logged triggered time 
        // context.getLogger().info("Azure Function completed: " + ???);
    }

    // TODO : pull this to super class
    @FunctionName("getMetrics")
    public HttpResponseMessage getMetrics(
            @HttpTrigger(name = "getMetrics", methods = {HttpMethod.GET}, route = "metrics") HttpRequestMessage<Optional<String>> request,
            final ExecutionContext context
    ) {
        return request.createResponseBuilder(HttpStatus.OK).body(monitoring.getMetrics()).build();
    }
}

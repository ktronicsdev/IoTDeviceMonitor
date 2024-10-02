package org.ktronics;

import com.microsoft.azure.functions.annotation.*;
import com.microsoft.azure.functions.*;
import io.micrometer.prometheus.PrometheusConfig;
import io.micrometer.prometheus.PrometheusMeterRegistry;
import org.ktronics.config.ConfigurationLoader;
import org.ktronics.config.ConfigurationManager;
import org.ktronics.models.Credential;
import org.ktronics.monitoring.FunctionMonitoring;
<<<<<<< HEAD
import org.ktronics.services.DatabaseService;
=======
>>>>>>> 707a10f3d13be20ba674ea7e19db0fb46f385d42
import org.ktronics.services.MongoDatabaseService;
import org.ktronics.services.PowerCheckService;

import java.util.List;
import java.util.Optional;

public class IoTDeviceMonitor {

    private final FunctionMonitoring monitoring;

    private ConfigurationManager configManager = new ConfigurationManager(new ConfigurationLoader());

    public IoTDeviceMonitor() {
        this.monitoring = new FunctionMonitoring(new PrometheusMeterRegistry(PrometheusConfig.DEFAULT));
    }

    @FunctionName("powerPlantMonitor")
    public void run(
            @TimerTrigger(name = "powerPlantMonitorTimerTrigger", schedule = "%TIMER_SCHEDULE%") String timerInfo,
            final ExecutionContext context
    ) {
        context.getLogger().info("Azure Function triggered: " + timerInfo);

<<<<<<< HEAD
        DatabaseService databaseService = new MongoDatabaseService();
=======
>>>>>>> 707a10f3d13be20ba674ea7e19db0fb46f385d42
        PowerCheckService powerCheckService = new PowerCheckService();
        List<Credential> credentials = new MongoDatabaseService().getCredentials();

        try {
            credentials.forEach(credential -> {
                if ("ShineMonitor".equals(credential.getType())) {
                    // TODO : move the shine monitor check to abnormal detector instead of IoT monitor
                    var shineMonitorStatus = powerCheckService.checkPowerStationsForUser(credential);
                    for (var status : shineMonitorStatus) {
                        context.getLogger().info(status);
                    }
                }
            });
            monitoring.onSuccess();
        }
        catch (Exception e) {
            monitoring.onFailure();
             // TODO : Please log faliur occured time since u logged triggered time
//            context.getLogger().severe("Error occurred: " + e.getMessage(), time occured ??);
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

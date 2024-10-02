package org.ktronics.config;

import java.io.FileInputStream;
import java.util.Properties;

public class ConfigurationLoader {

    private final String filePath = System.getenv("CONFIG_FILE_PATH");

    public Properties loadConfig() {
        var properties = new Properties();
        try (var inputStream = new FileInputStream(filePath)) {
            properties.load(inputStream);
        } catch (Exception e) {
            throw new RuntimeException("Failed to load configuration from " + filePath, e);
        }
        return properties;
    }

}

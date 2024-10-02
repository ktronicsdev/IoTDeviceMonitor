package org.ktronics.config;

import java.util.Properties;

public class ConfigurationManager {

    private final ConfigurationLoader configLoader;
    private Properties config;

    public ConfigurationManager(ConfigurationLoader configLoader) {
        this.configLoader = configLoader;
        load();
    }

    private void load() {
        this.config = configLoader.loadConfig();
    }

    public String getConfigValue(String key) {
        return config.getProperty(key);
    }

    public void reload() {
        load();
    }
}


package org.ktronics.models;

import java.util.UUID;

public final class PowerPlant {
    private String id;
    private String credentialId;
    private String powerPlantId;
    private String powerPlantName;
    private Integer isAbnormal;
    private Double abnormalPercentage;
    private Integer isMailSent;

    public PowerPlant(String id, String credentialId, String powerPlantId, String powerPlantName, Integer isAbnormal, Double abnormalPercentage, Integer isMailSent) {
        this.id = (id == null) ? UUID.randomUUID().toString() : id;
        this.credentialId = credentialId;
        this.powerPlantId = powerPlantId;
        this.powerPlantName = powerPlantName;
        this.isAbnormal = isAbnormal;
        this.abnormalPercentage = abnormalPercentage;
        this.isMailSent = isMailSent;
    }

    public PowerPlant() {
        this.id = UUID.randomUUID().toString();
        this.credentialId = null;
        this.powerPlantId = null; // or provide default
        this.powerPlantName = null; // or provide default
        this.isAbnormal = null;
        this.abnormalPercentage = null;
        this.isMailSent = null;
    }

    public PowerPlant(String powerPlantId, String powerPlantName) {
        this.id = UUID.randomUUID().toString();
        this.credentialId = null;
        this.powerPlantId = powerPlantId;
        this.powerPlantName = powerPlantName;
        this.isAbnormal = null;
        this.abnormalPercentage = null;
        this.isMailSent = null;
    }

    public String getId() {
        return id;
    }

    public String getCredentialId() {
        return credentialId;
    }

    public String getPowerPlantId() {
        return powerPlantId;
    }

    public String getPowerPlantName() {
        return powerPlantName;
    }

    public Integer getIsAbnormal() {
        return isAbnormal;
    }

    public Double getAbnormalPercentage() {
        return abnormalPercentage;
    }

    public Integer getIsMailSent() {
        return isMailSent;
    }

    public void setId(String id) {
        this.id = id;
    }

    public void setCredentialId(String credentialId) {
        this.credentialId = credentialId;
    }

    public void setPowerPlantId(String powerPlantId) {
        this.powerPlantId = powerPlantId;
    }

    public void setPowerPlantName(String powerPlantName) {
        this.powerPlantName = powerPlantName;
    }

    public void setAbnormalPercentage(Double abnormalPercentage) {
        this.abnormalPercentage = abnormalPercentage;
    }

    public void setIsAbnormal(Integer isAbnormal) {
        this.isAbnormal = isAbnormal;
    }

    public void setIsMailSent(Integer isMailSent) {
        this.isMailSent = isMailSent;
    }
}

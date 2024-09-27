package org.ktronics.models;

public final class PowerPlant {
    private Integer userId;
    private final String powerPlantId;
    private final String powerPlantName;
    private Integer isAbnormal;
    private Double abnormalPercentage;
    private Integer isMailSent;

    public PowerPlant(Integer userId, String powerPlantId, String powerPlantName, Integer isAbnormal, Double abnormalPercentage, Integer isMailSent) {
        this.userId = userId;
        this.powerPlantId = powerPlantId;
        this.powerPlantName = powerPlantName;
        this.isAbnormal = isAbnormal;
        this.abnormalPercentage = abnormalPercentage;
        this.isMailSent = isMailSent;
    }

    public PowerPlant(String powerPlantId, String powerPlantName) {
        this.userId = null;
        this.powerPlantId = powerPlantId;
        this.powerPlantName = powerPlantName;
        this.isAbnormal = null;
        this.abnormalPercentage = null;
        this.isMailSent = null;
    }

    public Integer getUserId() {
        return userId;
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

    public void setUserId(Integer userId) {
        this.userId = userId;
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
package org.ktronics.models;

public class PowerPlant {
    private Integer userId;
    private String powerPlantId;
    private String status;
    private Integer mailSent;

    public PowerPlant(Integer userId, String powerPlantId, String status, Integer mailSent) {
        this.userId = userId;
        this.powerPlantId = powerPlantId;
        this.status = status;
        this.mailSent = mailSent;
    }

    public Integer getUserId() {
        return userId;
    }

    public String getPowerPlantId() {
        return powerPlantId;
    }

    public String getStatus() {
        return status;
    }

    public Integer getMailSent() {
        return mailSent;
    }

}

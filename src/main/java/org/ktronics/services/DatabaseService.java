package org.ktronics.services;

import org.ktronics.models.Credential;
import org.ktronics.models.PowerPlant;

import java.util.List;

public interface DatabaseService {

    List<Credential> getCredentials();

    void updatePowerPlantStatus(PowerPlant powerPlant);


}

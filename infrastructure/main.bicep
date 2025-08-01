// infrastructure/main.bicep
targetScope = 'subscription'

@description('Name of the shared-services resource group to create')
param sharedRgName string  // e.g. "rg-quizzical-shared"
@description('Deployment location (use a low-cost West region)')
param location string = 'westus2'  // Default to West US 2 (low cost, West region)
@description('Project/application name for naming resources')
param projectName string = 'quizzical'
@description('Environment identifier for shared resources')
param environment string = 'shared'

// Create the dedicated shared-services Resource Group
resource sharedRG 'Microsoft.Resources/resourceGroups@2022-09-01' = {
  name: sharedRgName
  location: location
}

// Deploy the config module into the new resource group
module configModule './modules/config.bicep' = {
  name: 'SharedServicesConfig'
  scope: sharedRG    // Deploy within the created RG
  params: {
    location: location
    projectName: projectName
    environment: environment
  }
}

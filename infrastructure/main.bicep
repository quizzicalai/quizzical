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

// ---------------------------------------------------------------------------
// API Container App autoscale policy (P1 — Scalability)
// ---------------------------------------------------------------------------
// IMPORTANT — APPLICABILITY: this main.bicep is SUBSCRIPTION-SCOPED and only
// provisions shared services (resource group + Key Vault + App Configuration
// via modules/config.bicep). The API **Container App itself is NOT defined in
// bicep**: it is built and deployed by the GitHub Actions pipeline
// (.github/workflows/api-deploy.yml) using `azure/container-apps-deploy-action`,
// which creates/updates the app imperatively. That action does NOT consume
// this template, so the parameters below are **advisory / documentation only**
// today — declaring them does not by itself attach a scale rule to the running
// app.
//
// Until the Container App is migrated into IaC, apply this policy imperatively
// (e.g. in the deploy pipeline, after the container-apps-deploy step):
//
//   az containerapp update -g rg-quizzical-shared -n api-quizzical-dev \
//     --min-replicas 1 --max-replicas 5 \
//     --scale-rule-name http-concurrency \
//     --scale-rule-type http \
//     --scale-rule-http-concurrency 50
//
// These params are surfaced as outputs so a future Container App bicep module
// (or a CI step) can consume the single source of truth instead of hardcoding.
@description('Minimum API Container App replicas (keep >=1 so the master/uvicorn worker is always warm).')
param apiMinReplicas int = 1
@description('Maximum API Container App replicas the HTTP-concurrency rule may scale out to.')
param apiMaxReplicas int = 5
@description('Concurrent HTTP requests per replica that triggers scale-out (KEDA http scaler).')
param apiHttpScaleConcurrency int = 50

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

// Advisory autoscale policy for the API Container App. See the IMPORTANT note
// above: this is the intended `scale` block (min/max replicas + an HTTP
// concurrency rule) that should be attached to the Container App once it is
// defined in bicep. Exposed as an output so it is discoverable and consumable
// rather than buried in pipeline flags.
output apiAutoscalePolicy object = {
  minReplicas: apiMinReplicas
  maxReplicas: apiMaxReplicas
  rules: [
    {
      name: 'http-concurrency'
      http: {
        metadata: {
          concurrentRequests: '${apiHttpScaleConcurrency}'
        }
      }
    }
  ]
}

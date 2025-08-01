// infrastructure/modules/config.bicep
@description('Azure region for the shared services')
param location string
@description('Project/application name (for naming resources)')
param projectName string
@description('Environment or usage tag (e.g. shared, dev, prod)')
param environment string

// Derive resource names using standard naming conventions
var keyVaultName  = toLower('${projectName}-${environment}-kv')     // e.g. "quizzical-shared-kv"
var appConfigName = toLower('${projectName}-${environment}-appcs')  // e.g. "quizzical-shared-appcs"

// Azure Key Vault (Standard SKU) with RBAC authorization enabled
resource keyVault 'Microsoft.KeyVault/vaults@2023-02-01' = {
  name: keyVaultName
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'    // 'A' family for standard vault
      name: 'standard'
    }
    // Use Azure RBAC instead of access policies for Key Vault:
    enableRbacAuthorization: true
    accessPolicies: []      // No access policies; Azure RBAC will be used:contentReference[oaicite:2]{index=2}
    // Security configurations
    enableSoftDelete: true  // Soft-delete protection on (default)
    // enabledForDeployment / enabledForTemplateDeployment could be set to false by default for least privilege
    // Network access is left open (Enabled) for now; can be restricted later with Private Endpoints.
  }
}

// Azure App Configuration Store (Standard SKU)
resource appConfig 'Microsoft.AppConfiguration/configurationStores@2023-03-01' = {
  name: appConfigName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    disableLocalAuth: true       // Disable shared key auth; use Azure AD RBAC only:contentReference[oaicite:3]{index=3}
    // enablePurgeProtection: true  // (Optional) Enable Purge Protection for extra safety
    // publicNetworkAccess: 'Enabled' // Keep public access for now (can set 'Disabled' when using private endpoints)
  }
}

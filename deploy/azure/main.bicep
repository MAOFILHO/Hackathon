// AI Financial Agent on Azure Container Apps (Consumption plan): one container, HTTPS link, scales to zero.
// Deploy with deploy.sh; it builds and pushes the image and passes the secrets in through a private parameters file.

targetScope = 'resourceGroup'

@description('Container image to run, for example docker.io/<user>/ai-financial-agent:<tag>. It must be linux/amd64.')
param image string

@description('Name of the container app. It also prefixes the other resources.')
@minLength(2)
@maxLength(32)
param appName string = 'ai-financial-agent'

param location string = resourceGroup().location

@description('The shared password the team types in the browser. Stored as a Container Apps secret.')
@secure()
param appPassword string

@description('Anthropic API key. Stored as a Container Apps secret.')
@secure()
param anthropicApiKey string

@description('Only needed when the Anthropic key is not scoped to a workspace.')
param anthropicWorkspaceId string = ''

// Logs for troubleshooting. Ingestion is capped at 1 GB a day to bound the cost.
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${appName}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    workspaceCapping: {
      dailyQuotaGb: 1
    }
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${appName}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 7860
        transport: 'auto'
        allowInsecure: false
      }
      secrets: [
        {
          name: 'app-password'
          value: appPassword
        }
        {
          name: 'anthropic-api-key'
          value: anthropicApiKey
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'app'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          // No HTTP probe on purpose: every page, /api/health included, answers 401 without the password,
          // so a probe would fail and restart the app. The default port check is enough.
          env: concat([
            {
              name: 'APP_PASSWORD'
              secretRef: 'app-password'
            }
            {
              name: 'ANTHROPIC_API_KEY'
              secretRef: 'anthropic-api-key'
            }
          ], empty(anthropicWorkspaceId) ? [] : [
            {
              name: 'ANTHROPIC_WORKSPACE_ID'
              value: anthropicWorkspaceId
            }
          ])
        }
      ]
      scale: {
        minReplicas: 0 // costs nothing while nobody uses it; the first visit after a quiet spell takes 10 to 30 s
        maxReplicas: 1 // the database lives inside the container, so a second copy would not share it
      }
    }
  }
}

output appUrl string = 'https://${app.properties.configuration.ingress.fqdn}'

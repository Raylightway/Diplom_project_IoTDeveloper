# api-spec.yaml.tpl

openapi: 3.0.0
info:
  title: Data Ingestion API
  version: 1.0.0
paths:
  /data:
    post:
      summary: Ingest data
      operationId: ingestData
      x-yc-apigateway-integration:
        type: cloud-functions
        function_id: ${function_id}
        service_account_id: ${service_account_id}
      responses:
        '200':
          description: OK
        '500':
          description: Internal Server Error
  
  /api/data:
    get:
      summary: Get dashboard data
      operationId: getDashboardData
      parameters:
        - name: hours
          in: query
          schema:
            type: integer
          required: false
        - name: device
          in: query
          schema:
            type: string
          required: false
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${function_id}
        service_account_id: ${service_account_id}
      responses:
        '200':
          description: OK
          
  /api/aggregated:
    get:
      summary: Get aggregated data
      operationId: getAggregatedData
      parameters:
        - name: hours
          in: query
          schema:
            type: integer
          required: false
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${function_id}
        service_account_id: ${service_account_id}
      responses:
        '200':
          description: OK

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
      responses:
        '200':
          description: OK
        '500':
          description: Internal Server Error


{{- define "mtgmods-license.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mtgmods-license.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "mtgmods-license.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mtgmods-license.labels" -}}
helm.sh/chart: {{ include "mtgmods-license.chart" . }}
{{ include "mtgmods-license.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "mtgmods-license.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mtgmods-license.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "mtgmods-license.componentLabels" -}}
{{ include "mtgmods-license.selectorLabels" . }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "mtgmods-license.secretName" -}}
{{- if .Values.secrets.create }}
{{- printf "%s-secrets" (include "mtgmods-license.fullname" .) }}
{{- else }}
{{- required "secrets.existingSecret is required when secrets.create=false" .Values.secrets.existingSecret }}
{{- end }}
{{- end }}

{{- define "mtgmods-license.podTemplate" -}}
metadata:
  {{- if .Values.gitops.enabled }}
  annotations:
    checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
  {{- end }}
  labels:
    app.kubernetes.io/version: {{ .Values.config.appVersion | quote }}
    {{- include "mtgmods-license.componentLabels" (dict "Release" .Release "Chart" .Chart "Values" .Values "component" "api") | nindent 4 }}
spec:
  initContainers:
    - name: wait-for-schema
      image: {{ .Values.postgres.image | quote }}
      command:
        - sh
        - -c
        - until PGPASSWORD="$POSTGRES_PASSWORD" psql -h {{ include "mtgmods-license.fullname" . }}-postgres -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c 'SELECT 1 FROM licenses LIMIT 1' >/dev/null 2>&1; do sleep 2; done
      envFrom:
        - secretRef:
            name: {{ include "mtgmods-license.secretName" . }}
  containers:
    - name: license-service
      image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
      imagePullPolicy: {{ .Values.image.pullPolicy }}
      ports:
        - name: http
          containerPort: 8000
      envFrom:
        - configMapRef:
            name: {{ include "mtgmods-license.fullname" . }}-config
        - secretRef:
            name: {{ include "mtgmods-license.secretName" . }}
      env:
        # Keep the visible version bound to its ReplicaSet after abort.
        - name: APP_VERSION
          value: {{ .Values.config.appVersion | quote }}
        - name: INSTANCE_ID
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
      resources:
        {{- toYaml .Values.resources | nindent 8 }}
      readinessProbe:
        httpGet:
          path: /health
          port: http
        {{- toYaml .Values.probes.readiness | nindent 8 }}
      livenessProbe:
        httpGet:
          path: /health/live
          port: http
        {{- toYaml .Values.probes.liveness | nindent 8 }}
{{- end }}

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

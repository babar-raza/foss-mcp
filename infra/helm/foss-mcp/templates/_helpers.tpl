{{- define "foss-mcp.fullname" -}}
{{- printf "%s-%s-%s" .Chart.Name .Values.deployment.family .Values.deployment.platform | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "foss-mcp.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
foss-mcp.aspose.org/family: {{ .Values.deployment.family }}
foss-mcp.aspose.org/platform: {{ .Values.deployment.platform }}
{{- end }}

{{- define "foss-mcp.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

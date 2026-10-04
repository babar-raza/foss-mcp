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

# The serving Deployment and Service select on the component too. Ingestion Job pods share the
# name and instance labels, and without the component a Service would route to a Job pod that
# listens on nothing.
{{- define "foss-mcp.servingSelectorLabels" -}}
{{- include "foss-mcp.selectorLabels" . }}
app.kubernetes.io/component: serving
{{- end }}

{{- define "foss-mcp.manifestsClaimName" -}}
{{- .Values.manifests.claimName | default (printf "%s-manifests" (include "foss-mcp.fullname" .)) }}
{{- end }}

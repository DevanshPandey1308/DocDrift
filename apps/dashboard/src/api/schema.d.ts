export interface paths {
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Health */
        get: operations["health_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/webhooks/github": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Receive Github Webhook */
        post: operations["receive_github_webhook_webhooks_github_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/session": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Current Session */
        get: operations["current_session_auth_session_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/repositories": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Repositories */
        get: operations["repositories_repositories_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/repositories/{repository_id}/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Repository Runs */
        get: operations["repository_runs_repositories__repository_id__runs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Run Detail */
        get: operations["run_detail_runs__run_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/github/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Login */
        get: operations["login_auth_github_login_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/github/callback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Callback */
        get: operations["callback_auth_github_callback_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/suggestions/{suggestion_id}/feedback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Suggestion Feedback */
        post: operations["suggestion_feedback_suggestions__suggestion_id__feedback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/findings/{finding_id}/feedback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Finding Feedback List */
        get: operations["finding_feedback_list_findings__finding_id__feedback_get"];
        put?: never;
        /** Finding Feedback */
        post: operations["finding_feedback_findings__finding_id__feedback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/runs/{run_id}/feedback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Run Feedback */
        get: operations["run_feedback_runs__run_id__feedback_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/repositories/{repository_id}/feedback/metrics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Repository Metrics */
        get: operations["repository_metrics_repositories__repository_id__feedback_metrics_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/logout": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Logout */
        post: operations["logout_auth_logout_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** EvidenceResponse */
        EvidenceResponse: {
            /** Path */
            path: string;
            /** Line */
            line: number;
            /** Snippet */
            snippet: string;
        };
        /** FeedbackPage */
        FeedbackPage: {
            /** Items */
            items: components["schemas"]["FeedbackResponse"][];
            /** Limit */
            limit: number;
            /** Offset */
            offset: number;
        };
        /** FeedbackRequest */
        FeedbackRequest: {
            /**
             * Category
             * @enum {string}
             */
            category: "fix_accepted" | "fix_rejected" | "false_positive";
            /** Idempotency Key */
            idempotency_key: string;
            /** Reason */
            reason?: string | null;
        };
        /** FeedbackResponse */
        FeedbackResponse: {
            /** Id */
            id: number;
            /** Finding Id */
            finding_id: number;
            /** Run Id */
            run_id: number;
            /** Repository Id */
            repository_id: number;
            /** Category */
            category: string;
            /** Reason */
            reason: string | null;
            /** Actor Github User Id */
            actor_github_user_id: number | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
        };
        /** FindingResponse */
        FindingResponse: {
            /** Id */
            id: number;
            /** Claim Id */
            claim_id: string;
            /** Claim Type */
            claim_type: string;
            /** Subject */
            subject: string;
            /** Claim Text */
            claim_text: string;
            /** Document Path */
            document_path: string;
            /** Document Line */
            document_line: number;
            /** Verdict */
            verdict: string;
            /** Explanation */
            explanation: string;
            /** Verification Method */
            verification_method: string;
            /** Deterministic */
            deterministic: boolean;
            /** Ignored */
            ignored: boolean;
            /** Ignore Reason */
            ignore_reason: string | null;
            /** Evidence */
            evidence: components["schemas"]["EvidenceResponse"][];
            /** Suggestions */
            suggestions: components["schemas"]["SuggestionResponse"][];
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** HealthResponse */
        HealthResponse: {
            /**
             * Status
             * @constant
             */
            status: "healthy";
            /** Service */
            service: string;
            /** Version */
            version: string;
        };
        /** MetricsResponse */
        MetricsResponse: {
            /** Accepted Fixes */
            accepted_fixes: number;
            /** Rejected Fixes */
            rejected_fixes: number;
            /** False Positives */
            false_positives: number;
            /** Numerator */
            numerator: number;
            /** Denominator */
            denominator: number;
            /** Acceptance Rate */
            acceptance_rate: number | null;
        };
        /** RepositoryResponse */
        RepositoryResponse: {
            /** Id */
            id: number;
            /** Full Name */
            full_name: string;
        };
        /** RunDetailResponse */
        RunDetailResponse: {
            /** Id */
            id: number;
            /** Repository Id */
            repository_id: number;
            /** Pull Request Number */
            pull_request_number: number;
            /** Head Sha */
            head_sha: string;
            /** Base Sha */
            base_sha: string | null;
            /** Status */
            status: string;
            /** Publication Status */
            publication_status: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Findings Count */
            findings_count: number;
            /** False Count */
            false_count: number;
            /** Unverifiable Count */
            unverifiable_count: number;
            /** Findings */
            findings: components["schemas"]["FindingResponse"][];
        };
        /** RunListItem */
        RunListItem: {
            /** Id */
            id: number;
            /** Repository Id */
            repository_id: number;
            /** Pull Request Number */
            pull_request_number: number;
            /** Head Sha */
            head_sha: string;
            /** Base Sha */
            base_sha: string | null;
            /** Status */
            status: string;
            /** Publication Status */
            publication_status: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Findings Count */
            findings_count: number;
            /** False Count */
            false_count: number;
            /** Unverifiable Count */
            unverifiable_count: number;
        };
        /** SessionResponse */
        SessionResponse: {
            /** Github Login */
            github_login: string;
            /** Github User Id */
            github_user_id: number;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
        };
        /** SuggestionResponse */
        SuggestionResponse: {
            /** Id */
            id: number;
            /** Publication Status */
            publication_status: string;
            /** Document Path */
            document_path: string;
            /** Document Line */
            document_line: number;
            /** Head Sha */
            head_sha: string;
            /** Published At */
            published_at: string | null;
        };
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
            /** Input */
            input?: unknown;
            /** Context */
            ctx?: Record<string, never>;
        };
        /** WebhookError */
        WebhookError: {
            /** Code */
            code: string;
            /** Message */
            message: string;
        };
        /** WebhookErrorResponse */
        WebhookErrorResponse: {
            error: components["schemas"]["WebhookError"];
        };
        /** WebhookResponse */
        WebhookResponse: {
            /**
             * Status
             * @enum {string}
             */
            status: "accepted" | "duplicate" | "ignored";
            /** Event */
            event: string;
            /** Delivery Id */
            delivery_id: string;
            /** Message */
            message: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    health_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthResponse"];
                };
            };
        };
    };
    receive_github_webhook_webhooks_github_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WebhookResponse"];
                };
            };
            /** @description Delivery and run were persisted. */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WebhookResponse"];
                };
            };
            /** @description Invalid headers or payload. */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WebhookErrorResponse"];
                };
            };
            /** @description Missing or invalid signature. */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WebhookErrorResponse"];
                };
            };
            /** @description Required service configuration is unavailable. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WebhookErrorResponse"];
                };
            };
        };
    };
    current_session_auth_session_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionResponse"];
                };
            };
        };
    };
    repositories_repositories_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RepositoryResponse"][];
                };
            };
        };
    };
    repository_runs_repositories__repository_id__runs_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
            };
            header?: never;
            path: {
                repository_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunListItem"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    run_detail_runs__run_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                run_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunDetailResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    login_auth_github_login_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    callback_auth_github_callback_get: {
        parameters: {
            query: {
                code: string;
                state: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    suggestion_feedback_suggestions__suggestion_id__feedback_post: {
        parameters: {
            query?: never;
            header?: {
                "x-csrf-token"?: string | null;
            };
            path: {
                suggestion_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FeedbackRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FeedbackResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    finding_feedback_list_findings__finding_id__feedback_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
            };
            header?: never;
            path: {
                finding_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FeedbackPage"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    finding_feedback_findings__finding_id__feedback_post: {
        parameters: {
            query?: never;
            header?: {
                "x-csrf-token"?: string | null;
            };
            path: {
                finding_id: number;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FeedbackRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FeedbackResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    run_feedback_runs__run_id__feedback_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
            };
            header?: never;
            path: {
                run_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["FeedbackPage"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    repository_metrics_repositories__repository_id__feedback_metrics_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                repository_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MetricsResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    logout_auth_logout_post: {
        parameters: {
            query?: never;
            header?: {
                "x-csrf-token"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
}

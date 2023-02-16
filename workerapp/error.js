
class ZSTDDecompressionFailed extends Error {
    constructor(error_code, reason) {
        super(reason);
        this.error_code = error_code;
        this.reason = reason;
    }
}

class NoExternalTargetVehiclesError extends Error {
    constructor(reason) {
        super(reason);
        this.reason = reason;
    }
}

class PageEvaluationError extends Error {
    constructor(functionIdentifier, error) {
        super(error);
        this.functionIdentifier = functionIdentifier;
        this.error = error;
    }
}

class ShutdownRequestError extends Error {
    constructor(reason) {
        super(reason);
        this.reason = reason;
    }
}

class WebControlError extends Error {
    constructor(controlId, message, extraDetails = {}) {
        super(`Failed to find control Id: ${controlId}`);
        this.controlId = controlId;
        this.message = message;
        this.extraDetails = extraDetails;
    }
}

class WebControlsError extends Error {
    constructor(message, webControlsStatus = {}) {
        this.message = message;
        this.webControlsStatus = webControlsStatus;
    }
}

class GeneralError extends Error {
    constructor(err) {
        super(err.message);
        this.name = err.name;
        this.message = err.message;
        this.sourceInfo = {
            columnNumber: err.columnNumber || null,
            fileName: err.fileName || null,
            lineNumber: err.lineNumber || null,
            stack: err.stack || null
        };
    }
}

exports.ZSTDDecompressionFailed = ZSTDDecompressionFailed;
exports.PageEvaluationError = PageEvaluationError;
exports.ShutdownRequestError = ShutdownRequestError;
exports.WebControlError = WebControlError;
exports.GeneralError = GeneralError;

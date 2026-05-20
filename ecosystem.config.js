module.exports = {
  apps: [{
    name: "wompi-webhook",
    script: "wompi_odoo_webhook.py",
    interpreter: "python3",
    env: {
      NODE_ENV: "production",
      PYTHONPATH: ".",
    },
  }],
};
